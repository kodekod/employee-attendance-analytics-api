# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | | | | |

Also note anything you looked at and decided was **not** a defect, and why.


Defects Found in the Starter

#

Where (function / line)

What is wrong

How you'd notice it (test, input, or symptom)

How you fixed it

1

create_employee()

Checking for an existing employee before inserting is not atomic. Concurrent requests with the same emp_code can both pass the check.

Send concurrent POST requests using the same employee code; a duplicate-key exception may occur.

Enforce a unique MongoDB index on emp_code and handle duplicate-key errors as HTTP 409.

2

create_employee()

created_at is created using a naive local datetime, making timestamp interpretation inconsistent.

Create an employee and inspect the stored timestamp across environments with different time zones.

Use an explicit UTC timestamp for persisted instants.

3

compute_overtime()

The shift-end calculation assumes the shift ends on the attendance date. Overnight shifts can produce incorrect overtime calculations.

Test a shift such as 22:00–06:00 with a punch-out after midnight.

Determine the correct shift-end date, including overnight shifts, before calculating overtime.

4

punch_in()

Checking for an existing attendance record before inserting is vulnerable to concurrent requests.

Send two simultaneous punch-in requests for the same employee and date.

Enforce a unique compound index on (emp_code, date) and handle duplicate-key errors as HTTP 409.

5

monthly_analytics()

The calculation counts attendance statuses without restricting present days to working weekdays or accounting for an employee's joining date.

Test an employee who joined mid-month and records on weekends.

Calculate working-day attendance using the employee's joining date and the business rules.

6

Department trend endpoint

The starter exposes /analytics/department-trend with a months parameter, while the required API contract specifies /analytics/departments/{department}/trend with from and to dates.

Request the contract-defined URL with a date range.

Implement the exact contract route, inclusive daily rows, missing-date filling, moving averages, and date-range validation.

7

Admin explain endpoint

The explain API must validate the required parameters and query shape for each supported analytics endpoint. A mismatch can return an incorrect plan or accept invalid inputs.

Call explain endpoints with missing parameters or invalid date ranges.
