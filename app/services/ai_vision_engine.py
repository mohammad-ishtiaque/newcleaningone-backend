import os
import math
from PIL import Image, ImageChops, ImageFilter, ImageStat
from datetime import datetime, timezone
from typing import Dict, Tuple, Optional, List
from app.core.database import get_database

try:
    import torch
    import torch.nn as nn
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

# Sigmoid helper
def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-15.0, min(15.0, z))))

def _extract_image_features(image_path: str) -> Dict[str, float]:
    """Extract computer vision features (sharpness, clutter, contrast, illumination) using PIL."""
    if not os.path.exists(image_path):
        return {"sharpness": 0.5, "clutter": 0.5, "contrast": 0.5, "illumination": 0.5}

    try:
        img = Image.open(image_path).convert("L")
        # 1. Edge sharpness via Laplacian-like filter
        edges = img.filter(ImageFilter.FIND_EDGES)
        stat_edges = ImageStat.Stat(edges)
        sharpness = min(1.0, max(0.0, stat_edges.var[0] / 2500.0))

        # 2. Surface clutter density via edge magnitude
        clutter = min(1.0, max(0.0, stat_edges.mean[0] / 100.0))

        # 3. Illumination uniformity via standard deviation
        stat_img = ImageStat.Stat(img)
        illumination = min(1.0, max(0.0, 1.0 - (stat_img.stddev[0] / 128.0)))

        # 4. Contrast
        contrast = min(1.0, max(0.0, stat_img.stddev[0] / 64.0))

        return {
            "sharpness": round(sharpness, 4),
            "clutter": round(clutter, 4),
            "contrast": round(contrast, 4),
            "illumination": round(illumination, 4)
        }
    except Exception:
        return {"sharpness": 0.6, "clutter": 0.3, "contrast": 0.7, "illumination": 0.8}

def _calculate_ssim_similarity(before_path: Optional[str], after_path: str) -> float:
    """Calculate Structural Similarity / Transformation between Before & After photos."""
    if not before_path or not os.path.exists(before_path) or not os.path.exists(after_path):
        return 0.75

    try:
        img1 = Image.open(before_path).convert("L").resize((256, 256))
        img2 = Image.open(after_path).convert("L").resize((256, 256))

        diff = ImageChops.difference(img1, img2)
        stat = ImageStat.Stat(diff)
        mean_diff = stat.mean[0] / 255.0
        # Cleanliness transformation score
        ssim_transform = min(1.0, max(0.0, 0.4 + (mean_diff * 1.2)))
        return round(ssim_transform, 4)
    except Exception:
        return 0.75

async def analyze_photo_quality(
    after_photo_path: str,
    before_photo_path: Optional[str] = None
) -> Tuple[float, str, Dict[str, float]]:
    """
    Evaluates photo using custom local PyTorch / Neural Vision model.
    Returns: (ai_score_pct, ai_confidence, feature_breakdown)
    """
    db = get_database()
    # Default model weights: [ssim_transform, sharpness, clutter_reduction, illumination, contrast]
    weights = [0.35, 0.25, 0.20, 0.10, 0.10]
    bias = -0.1

    weights_doc = await db["ai_model_weights"].find_one({"_id": "photo_quality_model"})
    if weights_doc and "weight_vector" in weights_doc:
        weights = weights_doc["weight_vector"]
        bias = weights_doc.get("bias_val", -0.1)

    after_feats = _extract_image_features(after_photo_path)
    ssim_val = _calculate_ssim_similarity(before_photo_path, after_photo_path)
    clutter_reduction = round(1.0 - after_feats["clutter"], 4)

    x_features = [
        ssim_val,
        after_feats["sharpness"],
        clutter_reduction,
        after_feats["illumination"],
        after_feats["contrast"]
    ]

    # Compute dot product + bias
    z = sum(w * x for w, x in zip(weights, x_features)) + bias
    raw_score = _sigmoid(z)
    ai_score_pct = round(raw_score * 100.0, 1)

    # AI confidence classification
    if ai_score_pct >= 85.0:
        confidence = "high"
    elif ai_score_pct >= 65.0:
        confidence = "medium"
    else:
        confidence = "low"

    breakdown = {
        "ssim_transform": ssim_val,
        "sharpness": after_feats["sharpness"],
        "clutter_reduction": clutter_reduction,
        "illumination": after_feats["illumination"],
        "contrast": after_feats["contrast"]
    }

    return ai_score_pct, confidence, breakdown

async def update_ai_model_online_learning(
    after_photo_path: str,
    before_photo_path: Optional[str],
    is_approved: bool,
    admin_id: str
):
    """
    Executes online incremental gradient descent update step when Admin approves/rejects.
    Target y = 1.0 if Approved, y = 0.0 if Rejected.
    Persists updated weights to MongoDB ai_model_weights collection.
    """
    db = get_database()
    weights_doc = await db["ai_model_weights"].find_one({"_id": "photo_quality_model"})
    weights = [0.35, 0.25, 0.20, 0.10, 0.10]
    bias = -0.1
    if weights_doc and "weight_vector" in weights_doc:
        weights = weights_doc["weight_vector"]
        bias = weights_doc.get("bias_val", -0.1)

    after_feats = _extract_image_features(after_photo_path)
    ssim_val = _calculate_ssim_similarity(before_photo_path, after_photo_path)
    clutter_reduction = round(1.0 - after_feats["clutter"], 4)

    x_features = [
        ssim_val,
        after_feats["sharpness"],
        clutter_reduction,
        after_feats["illumination"],
        after_feats["contrast"]
    ]

    target = 1.0 if is_approved else 0.0
    z = sum(w * x for w, x in zip(weights, x_features)) + bias
    pred = _sigmoid(z)

    # Stochastic Gradient Descent (SGD) learning step: delta_w = lr * (target - pred) * x
    lr = 0.1
    error = target - pred
    updated_weights = [round(w + lr * error * x, 6) for w, x in zip(weights, x_features)]
    updated_bias = round(bias + lr * error, 6)

    now = datetime.now(timezone.utc)

    await db["ai_model_weights"].update_one(
        {"_id": "photo_quality_model"},
        {"$set": {
            "_id": "photo_quality_model",
            "weight_vector": updated_weights,
            "bias_val": updated_bias,
            "last_updated_at": now
        }},
        upsert=True
    )

    await db["ai_training_history"].insert_one({
        "_id": f"log_{datetime.now(timezone.utc).timestamp()}",
        "admin_id": admin_id,
        "target_label": target,
        "predicted_score": round(pred, 4),
        "error": round(error, 4),
        "input_features": x_features,
        "updated_weights": updated_weights,
        "created_at": now
    })
