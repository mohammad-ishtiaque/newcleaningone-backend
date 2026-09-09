from typing import List, Dict, Any

def filter_dynamic_tasks_and_photos(room_data: dict, visit_number: int) -> dict:
    """
    Filters tasks and required_photos from room_data based on their frequency_type
    and the current visit_number for the month.
    """
    monthly_frequency = room_data.get("monthly_cleaning_frequency", 0)
    if monthly_frequency < 1:
        monthly_frequency = 1

    tasks = room_data.get("tasks", [])
    photos = room_data.get("required_photos", [])

    filtered_tasks = []
    for task in tasks:
        # dict vs model handling
        task_freq = task.get("frequency_type", "every_visit") if isinstance(task, dict) else getattr(task, "frequency_type", "every_visit")
        if _should_include(task_freq, monthly_frequency, visit_number):
            filtered_tasks.append(task)

    filtered_photos = []
    for photo in photos:
        photo_freq = photo.get("frequency_type", "every_visit") if isinstance(photo, dict) else getattr(photo, "frequency_type", "every_visit")
        if _should_include(photo_freq, monthly_frequency, visit_number):
            filtered_photos.append(photo)

    room_data["tasks"] = filtered_tasks
    room_data["required_photos"] = filtered_photos
    return room_data

def _should_include(frequency_type: str, monthly_frequency: int, visit_number: int) -> bool:
    if not frequency_type:
        return True
    
    frequency_type = frequency_type.lower().strip()
    
    if frequency_type == "every_visit":
        return True
        
    if frequency_type == "monthly":
        # Only on the first visit of the month
        return visit_number == 1
        
    if frequency_type == "weekly":
        # Weekly implies 4 times a month. 
        if monthly_frequency <= 4:
            return True
        else:
            step = monthly_frequency // 4
            if step < 1:
                step = 1
            return (visit_number - 1) % step == 0
            
    if frequency_type == "biweekly":
        # Happens 2 times a month.
        if monthly_frequency <= 2:
            return True
        else:
            step = monthly_frequency // 2
            if step < 1:
                step = 1
            return (visit_number - 1) % step == 0
            
    if frequency_type == "yearly":
        # Simplified: just first visit
        return visit_number == 1
        
    return True
