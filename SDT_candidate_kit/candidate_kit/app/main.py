"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from decimal import Decimal, ROUND_HALF_UP

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo import MongoClient
from datetime import date
from zoneinfo import ZoneInfo

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    if punch_in.tzinfo is None:
        punch_ist = punch_in.replace(tzinfo=timezone.utc).astimezone(IST)
    else:
        punch_ist = punch_in.astimezone(IST)

    h, m = map(int, shift_start.split(":"))
    start = punch_ist.replace(
        hour=h, minute=m, second=0, microsecond=0
    )

    minutes = int((punch_ist - start).total_seconds() // 60)
    return minutes if minutes > 10 else 0



def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(str((punch_out - punch_in).total_seconds()))
    hours = (seconds / Decimal("3600")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return float(hours)




def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    if punch_out.tzinfo is None:
        punch_out_ist = punch_out.replace(
            tzinfo=timezone.utc
        ).astimezone(IST)
    else:
        punch_out_ist = punch_out.astimezone(IST)

    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(
        hour=h, minute=m, second=0, microsecond=0, tzinfo=IST
    )

    minutes = int((punch_out_ist - end).total_seconds() // 60)
    return minutes if minutes >= 30 else 0



# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #



class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(
        min_length=3,
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )
    department: str = Field(min_length=1, max_length=50)

    shift_start: str = Field(
        default="09:30",
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    )
    shift_end: str = Field(
        default="18:30",
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    )

    joined_on: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$"
    )

    @model_validator(mode="after")
    def validate_shift_times(self):
        if self.shift_start == self.shift_end:
            raise ValueError(
                "shift_start and shift_end must be different"
            )
        return self

    @model_validator(mode="after")
    def validate_joined_on(self):
        try:
            date.fromisoformat(self.joined_on)
        except ValueError:
            raise ValueError(
                "joined_on must be a valid date in YYYY-MM-DD format"
            )
        return self



class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    emp_code: str
    punched_at: Optional[int] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000
    )
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"



class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    emp_code: str
    punched_at: Optional[int] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000
    )


class AttendanceRegularizeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Optional[Literal[
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    ]] = None
    punch_in: Optional[int] = Field(
        default=None, ge=100000000000, le=4102444800000
    )
    punch_out: Optional[int] = Field(
        default=None, ge=100000000000, le=4102444800000
    )
    by: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now()
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc



@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size
    }



@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    ts = (
        datetime.fromtimestamp(
            body.punched_at / 1000, tz=timezone.utc
        )
        if body.punched_at is not None
        else datetime.now(timezone.utc)
    ).replace(microsecond=0)

    d = ts.astimezone(IST).date().isoformat()

    if db.attendance_logs.find_one({
        "emp_code": body.emp_code,
        "date": d
    }):
        raise HTTPException(409, "already punched in for this date")

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            ts, emp["shift_start"]
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    db.attendance_logs.insert_one(doc)

    response = {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": int(ts.timestamp() * 1000),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": doc["late_minutes"],
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    return response

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="page must be >= 1 and page_size must be between 1 and 100"
        )

    for value in (date_from, date_to):
        if value is not None:
            try:
                if datetime.strptime(value, "%Y-%m-%d").date().isoformat() != value:
                    raise ValueError
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail="Dates must use YYYY-MM-DD format"
                )

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must not be after date_to"
        )

    q = {}

    if emp_code:
        q["emp_code"] = emp_code

    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to

    if status:
        q["status"] = status

    total = db.attendance_logs.count_documents(q)

    docs = list(
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    def to_epoch_ms(value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1000)

    for doc in docs:
        for field in ("punch_in", "punch_out"):
            doc[field] = to_epoch_ms(doc.get(field))

        for entry in doc.get("history", []):
            if entry.get("at") is not None:
                entry["at"] = to_epoch_ms(entry["at"])

            changes = entry.get("changes", {})
            if isinstance(changes, dict):
                for field in ("punch_in", "punch_out"):
                    if field in changes:
                        changes[field] = to_epoch_ms(changes[field])

    return {
        "items": docs,
        "total": total,
        "page": page,
        "page_size": page_size
    }



@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    # 1. Check employee
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found"
        )

    # 2. Convert timestamp to UTC and truncate microseconds
    ts = (
        datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc
        )
        if body.punched_at is not None
        else datetime.now(timezone.utc)
    ).replace(microsecond=0)

    # MongoDB stores UTC datetimes; compare using a naive UTC value
    ts_db = ts.replace(tzinfo=None)

    # 3. Find the latest open attendance record
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts_db},
            "punch_out": None,
            "status": {"$in": ["PRESENT", "WFH", "ON_DUTY"]}
        },
        sort=[("punch_in", -1)]
    )

    # 4. If no earlier open record exists, check for a future punch-in
    if record is None:
        future_record = db.attendance_logs.find_one(
            {
                "emp_code": body.emp_code,
                "punch_in": {"$gt": ts_db},
                "punch_out": None,
                "status": {"$in": ["PRESENT", "WFH", "ON_DUTY"]}
            },
            sort=[("punch_in", 1)]
        )

        if future_record:
            raise HTTPException(
                status_code=422,
                detail="punch_out must be after punch_in"
            )

        # Check whether a previous record exists
        any_record = db.attendance_logs.find_one(
            {
                "emp_code": body.emp_code,
                "punch_in": {"$lte": ts_db}
            },
            sort=[("punch_in", -1)]
        )

        if any_record:
            raise HTTPException(
                status_code=409,
                detail="Already punched out or no open attendance record"
            )

        raise HTTPException(
            status_code=404,
            detail="No matching punch-in found"
        )

    # 5. Validate punch-in and punch-out timestamps
    punch_in_ts = record["punch_in"]

    if punch_in_ts.tzinfo is None:
        punch_in_ts = punch_in_ts.replace(tzinfo=timezone.utc)
    else:
        punch_in_ts = punch_in_ts.astimezone(timezone.utc)

    if ts <= punch_in_ts:
        raise HTTPException(
            status_code=422,
            detail="punch_out must be after punch_in"
        )

    duration = ts - punch_in_ts

    if duration > timedelta(hours=24):
        raise HTTPException(
            status_code=422,
            detail="punch_out cannot be more than 24 hours after punch_in"
        )

    # 6. Calculate attendance metrics
    work_hours = compute_work_hours(punch_in_ts, ts)

    overtime_minutes = compute_overtime(
        ts,
        emp["shift_end"],
        record["date"]
    )

    half_day = work_hours < 4.50

    # 7. Update only if the record is still open
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None
        },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day
            }
        }
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=409,
            detail="Attendance record was already updated"
        )

    # 8. Fetch updated record without MongoDB _id
    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0}
    )

    # 9. Convert timestamps to epoch milliseconds for the response
    for field in ("punch_in", "punch_out"):
        value = updated.get(field)

        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            else:
                value = value.astimezone(timezone.utc)

            updated[field] = int(value.timestamp() * 1000)

    for entry in updated.get("history", []):
        value = entry.get("at")

        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            else:
                value = value.astimezone(timezone.utc)

            entry["at"] = int(value.timestamp() * 1000)

    return updated




@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: AttendanceRegularizeIn
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date
    })
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    changes = {}
    update_fields = {}

    if body.status is not None and body.status != record.get("status"):
        changes["status"] = {
            "from": record.get("status"),
            "to": body.status
        }
        update_fields["status"] = body.status

    for field in ("punch_in", "punch_out"):
        value = getattr(body, field)
        if value is not None:
            new_value = datetime.fromtimestamp(
                value / 1000, tz=timezone.utc
            ).replace(microsecond=0)
            old_value = record.get(field)

            if old_value is not None and old_value.tzinfo is None:
                old_value = old_value.replace(tzinfo=timezone.utc)

            if old_value != new_value:
                changes[field] = {
                    "from": int(old_value.timestamp() * 1000)
                    if old_value else None,
                    "to": int(new_value.timestamp() * 1000)
                }
                update_fields[field] = new_value.replace(tzinfo=None)

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="No changes provided"
        )

    new_status = update_fields.get("status", record.get("status"))
    new_punch_in = update_fields.get("punch_in", record.get("punch_in"))
    new_punch_out = update_fields.get("punch_out", record.get("punch_out"))

    if new_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                status_code=422,
                detail="ABSENT/LEAVE cannot have punch times"
            )
        update_fields["punch_in"] = None
        update_fields["punch_out"] = None
        update_fields["work_hours"] = None
        update_fields["late_minutes"] = 0
        update_fields["overtime_minutes"] = 0
        update_fields["half_day"] = False
    else:
        if new_punch_in is None:
            raise HTTPException(
                status_code=422,
                detail="Presence status requires punch_in"
            )

        if new_punch_out is not None:
            if new_punch_out <= new_punch_in:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be after punch_in"
                )

            duration = (new_punch_out - new_punch_in).total_seconds()
            if duration > 24 * 60 * 60:
                raise HTTPException(
                    status_code=422,
                    detail="Punch duration cannot exceed 24 hours"
                )

            work_hours = compute_work_hours(
                new_punch_in, new_punch_out
            )
            update_fields["work_hours"] = work_hours
            update_fields["overtime_minutes"] = compute_overtime(
                new_punch_out, emp["shift_end"], date
            )
            update_fields["half_day"] = work_hours < 4.50
        else:
            update_fields["work_hours"] = None
            update_fields["overtime_minutes"] = 0
            update_fields["half_day"] = False

        update_fields["late_minutes"] = compute_late_minutes(
            new_punch_in, emp["shift_start"]
        )

    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.by,
        "reason": body.reason,
        "changes": changes
    }

    result = db.attendance_logs.update_one(
        {"_id": record["_id"]},
        {
            "$set": update_fields,
            "$push": {"history": history_entry}
        }
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=409, detail="Update conflict")

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]}, {"_id": 0}
    )

    for field in ("punch_in", "punch_out"):
        value = updated.get(field)
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            updated[field] = int(value.timestamp() * 1000)

    for entry in updated.get("history", []):
        if entry.get("at") is not None:
            at = entry["at"]
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            entry["at"] = int(at.timestamp() * 1000)

    return updated



@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    import re

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    year, mon = map(int, month.split("-"))
    if mon == 12:
        next_month = f"{year + 1}-01-01"
    else:
        next_month = f"{year}-{mon + 1:02d}-01"

    month_end = (date.fromisoformat(next_month) - timedelta(days=1)).isoformat()

    employee_match = {"joined_on": {"$lte": month_end}}
    if department:
        employee_match["department"] = department

    # Headcount includes employees who joined by month-end,
    # even if they have no attendance records.
    headcount_rows = list(db.employees.aggregate([
        {"$match": employee_match},
        {"$group": {
            "_id": "$department",
            "headcount": {"$sum": 1}
        }}
    ]))

    # Calculate attendance metrics from stored derived values.
    attendance_pipeline = [
        {"$match": {
            "date": {"$gte": f"{month}-01", "$lt": next_month}
        }},
        {"$lookup": {
            "from": "employees",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "as": "employee"
        }},
        {"$unwind": "$employee"},
    ]

    if department:
        attendance_pipeline.append({
            "$match": {"employee.department": department}
        })

    attendance_pipeline.extend([
        {"$addFields": {
            "day_of_week": {
                "$dayOfWeek": {
                    "$dateFromString": {"dateString": "$date"}
                }
            }
        }},
        {"$group": {
            "_id": "$employee.department",
            "present_days": {
                "$sum": {
                    "$cond": [
                        {
                            "$and": [
                                {"$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"]
                                ]},
                                {"$gte": ["$day_of_week", 2]},
                                {"$lte": ["$day_of_week", 6]}
                            ]
                        },
                        {"$cond": [
                            {"$eq": ["$half_day", True]},
                            0.5,
                            1
                        ]},
                        0
                    ]
                }
            },
            "avg_work_hours": {
                "$avg": {
                    "$cond": [
                        {
                            "$and": [
                                {"$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"]
                                ]},
                                {"$ne": ["$work_hours", None]}
                            ]
                        },
                        "$work_hours",
                        None
                    ]
                }
            },
            "late_count": {
                "$sum": {
                    "$cond": [
                        {"$gt": [
                            {"$ifNull": ["$late_minutes", 0]}, 0
                        ]},
                        1, 0
                    ]
                }
            },
            "total_late_minutes": {
                "$sum": {"$ifNull": ["$late_minutes", 0]}
            },
            "leave_count": {
                "$sum": {
                    "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                }
            },
            "on_duty_count": {
                "$sum": {
                    "$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]
                }
            }
        }}
    ])

    attendance_rows = list(
        db.attendance_logs.aggregate(attendance_pipeline)
    )

    # Merge departments with no attendance records as well.
    summary = {}

    for row in headcount_rows:
        dept = row["_id"]
        summary[dept] = {
            "department": dept,
            "headcount": row["headcount"],
            "present_days": 0,
            "avg_work_hours": None,
            "late_count": 0,
            "total_late_minutes": 0,
            "leave_count": 0,
            "on_duty_count": 0
        }

    for row in attendance_rows:
        dept = row["_id"]

        if dept not in summary:
            summary[dept] = {
                "department": dept,
                "headcount": 0,
                "present_days": 0,
                "avg_work_hours": None,
                "late_count": 0,
                "total_late_minutes": 0,
                "leave_count": 0,
                "on_duty_count": 0
            }

        for key in (
            "present_days",
            "avg_work_hours",
            "late_count",
            "total_late_minutes",
            "leave_count",
            "on_duty_count"
        ):
            summary[dept][key] = row.get(key)

    from decimal import Decimal, ROUND_HALF_UP

    for item in summary.values():
        if item["avg_work_hours"] is not None:
            item["avg_work_hours"] = float(
                Decimal(str(item["avg_work_hours"])).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP
                )
            )

    return {
        "month": month,
        "items": sorted(
            summary.values(),
            key=lambda item: item["department"]
        )
    }


    
@app.get("/analytics/monthly")
def monthly_analytics(month: str):
    import re

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    start_date = f"{month}-01"

    year, mon = map(int, month.split("-"))
    if mon == 12:
        next_month = f"{year + 1}-01-01"
    else:
        next_month = f"{year}-{mon + 1:02d}-01"

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": start_date,
                    "$lt": next_month
                }
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_records": {"$sum": 1},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"]
                                ]
                            },
                            1,
                            0
                        ]
                    }
                },
                "absent_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "ABSENT"]},
                            1,
                            0
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0
                        ]
                    }
                },
                "late_days": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0
                        ]
                    }
                },
                "total_work_hours": {
                    "$sum": {"$ifNull": ["$work_hours", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                }
            }
        },
        {"$sort": {"_id": 1}}
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": [
            {
                "emp_code": item["_id"],
                "total_records": item["total_records"],
                "present_days": item["present_days"],
                "absent_days": item["absent_days"],
                "leave_days": item["leave_days"],
                "late_days": item["late_days"],
                "total_work_hours": round(item["total_work_hours"], 2),
                "total_overtime_minutes": item["total_overtime_minutes"]
            }
            for item in results
        ]
    }



@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(emp_code: str, month: str):
    import re
    from decimal import Decimal, ROUND_HALF_UP

    # Validate month format
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    # Find employee
    emp = db.employees.find_one({"emp_code": emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found"
        )

    # Calculate month boundaries
    year, mon = map(int, month.split("-"))
    start_date = f"{month}-01"

    if mon == 12:
        next_month = f"{year + 1}-01-01"
    else:
        next_month = f"{year}-{mon + 1:02d}-01"

    end_date = (
        date.fromisoformat(next_month) - timedelta(days=1)
    )

    # Count weekdays from joining date through month end
    joined_on = date.fromisoformat(emp["joined_on"])
    first_day = max(date.fromisoformat(start_date), joined_on)

    working_days = 0
    current_day = first_day

    while current_day <= end_date:
        if current_day.weekday() < 5:
            working_days += 1
        current_day += timedelta(days=1)

    # Aggregate attendance records in MongoDB
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": start_date,
                    "$lt": next_month
                }
            }
        },
        {
            "$addFields": {
                "_day_of_week": {
                    "$dayOfWeek": {
                        "$dateFromString": {
                            "dateString": "$date"
                        }
                    }
                }
            }
        },
        {
            "$group": {
                "_id": None,

                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"]
                                        ]
                                    },
                                    {"$gte": ["$_day_of_week", 2]},
                                    {"$lte": ["$_day_of_week", 6]}
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },

                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0
                        ]
                    }
                },

                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0
                        ]
                    }
                },

                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },

                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                }
            }
        }
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    if results:
        result = results[0]
    else:
        result = {
            "present_days": 0,
            "leave_days": 0,
            "late_count": 0,
            "total_late_minutes": 0,
            "total_overtime_minutes": 0
        }

    # Calculate attendance percentage
    if working_days == 0:
        attendance_pct = None
    else:
        percentage = (
            Decimal(str(result["present_days"]))
            / Decimal(working_days)
            * Decimal("100")
        )
        attendance_pct = float(
            percentage.quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP
            )
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": result["present_days"],
        "leave_days": result["leave_days"],
        "late_count": result["late_count"],
        "total_late_minutes": result["total_late_minutes"],
        "total_overtime_minutes": result["total_overtime_minutes"],
        "attendance_pct": attendance_pct
    }


@app.get("/analytics/department-summary")
def department_summary(month: str):
    import re

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    year, mon = map(int, month.split("-"))
    start_date = f"{month}-01"

    if mon == 12:
        next_month = f"{year + 1}-01-01"
    else:
        next_month = f"{year}-{mon + 1:02d}-01"

    pipeline = [
        {
            "$match": {
                "date": {"$gte": start_date, "$lt": next_month}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"},
        {
            "$group": {
                "_id": "$employee.department",
                "total_records": {"$sum": 1},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            1, 0
                        ]
                    }
                },
                "absent_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "ABSENT"]}, 1, 0]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                    }
                },
                "late_days": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1, 0
                        ]
                    }
                },
                "total_work_hours": {
                    "$sum": {"$ifNull": ["$work_hours", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                }
            }
        },
        {"$sort": {"_id": 1}}
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": [
            {
                "department": item["_id"],
                "total_records": item["total_records"],
                "present_days": item["present_days"],
                "absent_days": item["absent_days"],
                "leave_days": item["leave_days"],
                "late_days": item["late_days"],
                "total_work_hours": round(item["total_work_hours"], 2),
                "total_overtime_minutes": item["total_overtime_minutes"]
            }
            for item in results
        ]
    }




@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = 10,
    department: Optional[str] = None
):
    import re

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(422, "month must be in YYYY-MM format")

    if limit < 1 or limit > 50:
        raise HTTPException(422, "limit must be between 1 and 50")

    year, mon = map(int, month.split("-"))
    next_month = (
        f"{year + 1}-01-01" if mon == 12
        else f"{year}-{mon + 1:02d}-01"
    )

    pipeline = [
        {"$match": {
            "date": {"$gte": f"{month}-01", "$lt": next_month},
            "late_minutes": {"$gt": 0}
        }},
        {"$lookup": {
            "from": "employees",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "as": "employee"
        }},
        {"$unwind": "$employee"},
    ]

    if department is not None:
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": 1},
            "name": {"$first": "$employee.name"},
            "department": {"$first": "$employee.department"}
        }},
        {"$sort": {
            "total_late_minutes": -1,
            "_id": 1
        }},
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {
                "rank": {"$rank": {}}
            }
        }},
        {"$match": {"rank": {"$lte": limit}}},
        {"$project": {
            "_id": 0,
            "emp_code": "$_id",
            "name": 1,
            "department": 1,
            "late_count": 1,
            "total_late_minutes": 1,
            "rank": 1
        }}
    ])

    items = list(db.attendance_logs.aggregate(pipeline))

    return {"month": month, "items": items}



@app.get("/analytics/department-trend")
def department_trend(department: str, months: int = 6):
    import re

    if not department.strip():
        raise HTTPException(
            status_code=422,
            detail="department cannot be empty"
        )

    if months < 1 or months > 24:
        raise HTTPException(
            status_code=422,
            detail="months must be between 1 and 24"
        )

    now = datetime.now(IST)
    current_month = now.year * 12 + now.month - 1
    month_list = []

    for offset in range(months - 1, -1, -1):
        value = current_month - offset
        year = value // 12
        month_number = value % 12 + 1
        month_list.append(f"{year}-{month_number:02d}")

    first_month = month_list[0]
    last_month = month_list[-1]

    first_year, first_num = map(int, first_month.split("-"))
    if first_num == 1:
        start_date = f"{first_year - 1}-12-01"
    else:
        start_date = f"{first_year}-{first_num:02d}-01"

    last_year, last_num = map(int, last_month.split("-"))
    if last_num == 12:
        end_date = f"{last_year + 1}-01-01"
    else:
        end_date = f"{last_year}-{last_num + 1:02d}-01"

    pipeline = [
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"},
        {
            "$match": {
                "employee.department": department,
                "date": {"$gte": start_date, "$lt": end_date}
            }
        },
        {
            "$group": {
                "_id": {"$substr": ["$date", 0, 7]},
                "total_records": {"$sum": 1},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"]
                                ]
                            },
                            1,
                            0
                        ]
                    }
                },
                "late_days": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0
                        ]
                    }
                },
                "total_work_hours": {
                    "$sum": {"$ifNull": ["$work_hours", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                }
            }
        },
        {"$sort": {"_id": 1}}
    ]

    results = list(db.attendance_logs.aggregate(pipeline))
    result_map = {item["_id"]: item for item in results}

    items = []
    for month_value in month_list:
        item = result_map.get(month_value, {})
        items.append({
            "month": month_value,
            "total_records": item.get("total_records", 0),
            "present_days": item.get("present_days", 0),
            "late_days": item.get("late_days", 0),
            "total_work_hours": round(item.get("total_work_hours", 0), 2),
            "total_overtime_minutes": item.get("total_overtime_minutes", 0)
        })

    return {
        "department": department,
        "months": months,
        "items": items
    }


@app.get("/admin/explain")
def admin_explain():
    query = {"date": {"$gte": "2026-10-01", "$lt": "2026-11-01"}}

    plan = db.attendance_logs.find(query).sort(
        [("date", -1), ("emp_code", 1)]
    ).explain()

    return {
        "query": query,
        "execution_stats": plan.get("executionStats", {}),
        "winning_plan": plan.get("queryPlanner", {}).get(
            "winningPlan", {}
        )
    }


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    months: int = 6
):
    import re

    allowed = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend"
    }

    if endpoint not in allowed:
        raise HTTPException(
            status_code=422,
            detail="Unsupported endpoint"
        )

    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="Invalid pagination parameters"
        )

    if limit < 1 or limit > 50:
        raise HTTPException(
            status_code=422,
            detail="limit must be between 1 and 50"
        )

    def month_bounds(value):
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
            raise HTTPException(
                status_code=422,
                detail="month must be in YYYY-MM format"
            )

        year, mon = map(int, value.split("-"))

        next_month = (
            f"{year + 1}-01-01"
            if mon == 12
            else f"{year}-{mon + 1:02d}-01"
        )

        return f"{value}-01", next_month

    def explain_aggregate(collection, pipeline):
        return db.command(
            "explain",
            {
                "aggregate": collection,
                "pipeline": pipeline,
                "cursor": {}
            },
            verbosity="executionStats"
        )

    collection = "attendance_logs"

    if endpoint == "attendance_list":
        query = {}

        if emp_code:
            query["emp_code"] = emp_code

        if status:
            query["status"] = status

        if date_from or date_to:
            query["date"] = {}

            if date_from:
                query["date"]["$gte"] = date_from

            if date_to:
                query["date"]["$lte"] = date_to

        plan = (
            db.attendance_logs.find(query)
            .sort([("date", -1), ("emp_code", 1)])
            .skip((page - 1) * page_size)
            .limit(page_size)
            .explain()
        )

    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="emp_code and month are required"
            )

        start, end = month_bounds(month)

        pipeline = [
            {
                "$match": {
                    "emp_code": emp_code,
                    "date": {"$gte": start, "$lt": end}
                }
            },
            {"$sort": {"date": 1}}
        ]

        plan = explain_aggregate(collection, pipeline)

    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required"
            )

        start, end = month_bounds(month)

        pipeline = [
            {
                "$match": {
                    "date": {"$gte": start, "$lt": end}
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "employee"
                }
            },
            {"$unwind": "$employee"}
        ]

        if department:
            pipeline.append({
                "$match": {"employee.department": department}
            })

        pipeline.extend([
            {
                "$group": {
                    "_id": "$employee.department",
                    "total_records": {"$sum": 1},
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$in": [
                                        "$status",
                                        ["PRESENT", "WFH", "ON_DUTY"]
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },
                    "absent_days": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$status", "ABSENT"]},
                                1,
                                0
                            ]
                        }
                    },
                    "leave_days": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$status", "LEAVE"]},
                                1,
                                0
                            ]
                        }
                    },
                    "late_days": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$gt": [
                                        {"$ifNull": ["$late_minutes", 0]},
                                        0
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },
                    "total_work_hours": {
                        "$sum": {"$ifNull": ["$work_hours", 0]}
                    },
                    "total_overtime_minutes": {
                        "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                    }
                }
            },
            {"$sort": {"_id": 1}}
        ])

        plan = explain_aggregate(collection, pipeline)

    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required"
            )

        start, end = month_bounds(month)

        pipeline = [
            {
                "$match": {
                    "date": {"$gte": start, "$lt": end},
                    "late_minutes": {"$gt": 0}
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "employee"
                }
            },
            {"$unwind": "$employee"}
        ]

        if department:
            pipeline.append({
                "$match": {"employee.department": department}
            })

        pipeline.extend([
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                    "late_count": {"$sum": 1},
                    "name": {"$first": "$employee.name"},
                    "department": {"$first": "$employee.department"}
                }
            },
            {
                "$sort": {
                    "total_late_minutes": -1,
                    "_id": 1
                }
            },
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {"rank": {"$rank": {}}}
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {
                "$project": {
                    "_id": 0,
                    "emp_code": "$_id",
                    "name": 1,
                    "department": 1,
                    "late_count": 1,
                    "total_late_minutes": 1,
                    "rank": 1
                }
            }
        ])

        plan = explain_aggregate(collection, pipeline)

    else:
        if not department:
            raise HTTPException(
                status_code=422,
                detail="department is required"
            )

        if months < 1 or months > 24:
            raise HTTPException(
                status_code=422,
                detail="months must be between 1 and 24"
            )

        now = datetime.now(IST)
        current_month = now.year * 12 + now.month - 1

        month_list = []

        for offset in range(months - 1, -1, -1):
            value = current_month - offset
            year = value // 12
            month_number = value % 12 + 1
            month_list.append(f"{year}-{month_number:02d}")

        first_year, first_num = map(int, month_list[0].split("-"))
        last_year, last_num = map(int, month_list[-1].split("-"))

        start_date = f"{first_year}-{first_num:02d}-01"

        if last_num == 12:
            end_date = f"{last_year + 1}-01-01"
        else:
            end_date = f"{last_year}-{last_num + 1:02d}-01"

        collection = "attendance_logs"

        pipeline = [
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "employee"
                }
            },
            {"$unwind": "$employee"},
            {
                "$match": {
                    "employee.department": department,
                    "date": {"$gte": start_date, "$lt": end_date}
                }
            },
            {
                "$group": {
                    "_id": {"$substr": ["$date", 0, 7]},
                    "total_records": {"$sum": 1},
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$in": [
                                        "$status",
                                        ["PRESENT", "WFH", "ON_DUTY"]
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },
                    "late_days": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$gt": [
                                        {"$ifNull": ["$late_minutes", 0]},
                                        0
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    },
                    "total_work_hours": {
                        "$sum": {"$ifNull": ["$work_hours", 0]}
                    },
                    "total_overtime_minutes": {
                        "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                    }
                }
            },
            {"$sort": {"_id": 1}}
        ]

        plan = explain_aggregate(collection, pipeline)

    return {
        "endpoint": endpoint,
        "collection": collection,
        "explain": plan
    }



# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #

@app.on_event("startup")
def create_indexes():
    db.employees.create_index("emp_code", unique=True)

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True
    )

    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)]
    )