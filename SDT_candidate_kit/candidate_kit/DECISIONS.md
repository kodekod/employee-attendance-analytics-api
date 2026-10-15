# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.
3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
5. **One thing you would change** if this had to serve 100x the data.


1. Indexes

I created a unique index on employees.emp_code to prevent duplicate employee codes. I also created a unique compound index on attendance_logs (emp_code, date) to guarantee that an employee has at most one attendance record per day. A compound index on (date descending, emp_code ascending) supports date-based attendance listings and sorting. I considered adding a separate index on status, but rejected it because its usefulness depends on query frequency and selectivity; extra indexes also increase storage and write costs.

2. Punch-in Race

If two identical punch-in requests arrive simultaneously, both might initially find no existing record. The unique compound index is the final protection against duplicate records. One insert succeeds, while the other violates the unique constraint. The API should catch that duplicate-key error and return HTTP 409 instead of an unhandled server error.

3. Ties

The leaderboard sorts employees by total late minutes and then by employee code for deterministic ordering. If two employees tie at the cutoff, the result depends on whether the code uses a rank-based cutoff or a strict limit. The implementation should follow the assignment contract's tie-handling rule.

4. Headcount

The department summary aggregates employees separately from attendance logs. This allows employees who joined by the end of the month to count toward headcount even if they have no attendance records.

5. Scaling

For 100 times more data, I would measure query plans and latency first, then optimize aggregation pipelines and consider pagination or caching for expensive analytics.
