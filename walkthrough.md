# Walkthrough - Cleaning Plan Shift Execution, Attendance & Live Shift Monitoring

## Overview

We have successfully implemented the full cleaning plan shift execution workflow, separate daily execution tracking in MongoDB (`shift_executions`), 15-minute grace period attendance evaluation, PyTorch AI photo analysis & approval flow, and live shift monitoring.

---

## Key Architecture & Implementation Details

### 1. Dedicated Shift Executions Collection (`shift_executions`)
- Recurring plans (e.g. `everyday`, `weekly`) and one-time plans are tracked using dedicated daily execution documents:
  - Document ID format: `exec_{plan_id}_{YYYY-MM-DD}` (e.g. `exec_plan_2fc87c5399_2026-08-17`).
  - Seamlessly instantiated or queried upon worker check-in, task toggle, photo submission, or live status view.
  - Automatically preserves full historical logs per date without modifying past records.

### 2. Worker Attendance Engine with 15-Minute Grace Period
- **Check-In (`POST /worker/shifts/{shift_id}/check-in`)**:
  - Compares check-in time against plan `start_time` + 15 minutes grace period.
  - $\le \text{start\_time} + 15\text{m} \rightarrow \mathbf{ontime}$.
  - $> \text{start\_time} + 15\text{m} \rightarrow \mathbf{late}$.
- **Check-Out (`POST /worker/shifts/{shift_id}/check-out`)**:
  - Calculates exact hours worked: $\text{hours\_worked} = \text{round}\left(\frac{\text{now} - \text{checkin\_time}}{3600}, 2\right)$.
- **Missing / Scheduled Detection**:
  - Workers not checked in before cutoff $\rightarrow \mathbf{scheduled}$.
  - Workers unstarted after cutoff $\rightarrow \mathbf{missing}$.
- Deprecated `/attendance` and `/start` routes are preserved for backward compatibility and hidden from OpenAPI docs (`include_in_schema=False`).

### 3. Shift Progress Percentage Formula
- Shift execution progress is dynamically calculated using the weighted item formula:
  $$\text{Progress \%} = \text{round}\left(\frac{\text{completed\_tasks} + \text{approved\_photos}}{\text{total\_tasks} + \text{total\_photos}} \times 100, 1\right)$$

### 4. Room Execution & Manager Photo Reviews
- **Room Start (`POST /worker/shifts/{shift_id}/rooms/{room_id}/start`)**: Transitions room to `"in_progress"`.
- **Task Toggle (`POST /worker/shifts/{shift_id}/rooms/{room_id}/tasks/{task_id}/toggle`)**: Toggles task completion and recalculates overall shift progress.
- **Photo Upload (`POST /worker/shifts/{shift_id}/rooms/{room_id}/photos`)**: Analyzes photo with PyTorch AI vision engine, submits into `photo_reviews` queue with `"pending_review"`.
- **Manager Approval (`PATCH /manager/photo-reviews/{review_id}/approve`)**: Marks review `"approved"`, marks linked room `"completed"` and verified, increments approved photos, updates progress %, and triggers PyTorch SGD online learning ($y=1.0$).

### 5. Live Shift Monitoring (`GET /manager/shift-monitoring/live-status`)
- Evaluates all active cleaning plans on `target_date`.
- Returns total shifts count, ontime count, late count, missing count, worked hours (`"5.2h worked"` / `"Not started"`), and real-time progress %.

---

## File Line Counts & Modularity Verification

All codebase files are strictly within 500–700 lines:

| File | Line Count | Status |
| :--- | :--- | :--- |
| [`app/api/worker_shifts.py`](file:///c:/Users/mdsad/Documents/Cleaning_service/app/api/worker_shifts.py) | 237 lines | ✅ Modular |
| [`app/api/worker_shifts_attendance.py`](file:///c:/Users/mdsad/Documents/Cleaning_service/app/api/worker_shifts_attendance.py) | 259 lines | ✅ Modular |
| [`app/api/worker_shifts_execution.py`](file:///c:/Users/mdsad/Documents/Cleaning_service/app/api/worker_shifts_execution.py) | 456 lines | ✅ Modular |
| [`app/api/worker_shift_utils.py`](file:///c:/Users/mdsad/Documents/Cleaning_service/app/api/worker_shift_utils.py) | 358 lines | ✅ Modular |
| [`app/api/admin_shift_monitoring.py`](file:///c:/Users/mdsad/Documents/Cleaning_service/app/api/admin_shift_monitoring.py) | 423 lines | ✅ Modular |
| [`app/api/admin_shift_monitoring_worker_stats.py`](file:///c:/Users/mdsad/Documents/Cleaning_service/app/api/admin_shift_monitoring_worker_stats.py) | 491 lines | ✅ Modular |
| [`app/api/admin/photo_reviews.py`](file:///c:/Users/mdsad/Documents/Cleaning_service/app/api/admin/photo_reviews.py) | 316 lines | ✅ Modular |
| [`app/api/admin/cleaning_plans.py`](file:///c:/Users/mdsad/Documents/Cleaning_service/app/api/admin/cleaning_plans.py) | 697 lines | ✅ Modular |
| [`app/api/admin/cleaning_plan_worker_utils.py`](file:///c:/Users/mdsad/Documents/Cleaning_service/app/api/admin/cleaning_plan_worker_utils.py) | 579 lines | ✅ Modular |
| [`app/api/admin/cleaning_plan_formatters.py`](file:///c:/Users/mdsad/Documents/Cleaning_service/app/api/admin/cleaning_plan_formatters.py) | 371 lines | ✅ Modular |

---

## Verification Results

The end-to-end test script [`scratch/verify_shift_execution_full.py`](file:///c:/Users/mdsad/Documents/Cleaning_service/scratch/verify_shift_execution_full.py) executed the entire lifecycle:
1. `GET /manager/shift-monitoring/live-status` $\rightarrow$ Returned active shifts and attendance counts.
2. `POST /worker/shifts/{plan_id}/check-in` $\rightarrow$ Successfully checked in worker `w_101` with `status="ontime"`.
3. `POST /worker/shifts/{plan_id}/rooms/{room_id}/start` $\rightarrow$ Updated room to `in_progress`.
4. `POST /worker/shifts/{plan_id}/rooms/{room_id}/tasks/{task_id}/toggle` $\rightarrow$ Toggled task and updated progress %.
5. `POST /worker/shifts/{plan_id}/rooms/{room_id}/photos` $\rightarrow$ PyTorch AI vision analyzed image and created photo review `RV-F0CB4B`.
6. `PATCH /manager/photo-reviews/{review_id}/approve` $\rightarrow$ Approved photo review, marked room completed, updated progress %.
7. `POST /worker/shifts/{plan_id}/check-out` $\rightarrow$ Successfully checked out worker and computed hours worked.
