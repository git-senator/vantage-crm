"""Background jobs.

The rule that shapes everything in this package: **a job is a service call with
a different trigger**. Job functions resolve a session, bind tenant context, and
then call the same service methods a request would — they do not reimplement
business logic with looser rules, and they do not get a wider view of the data
than a user does.

That has one non-obvious consequence. RLS is enabled on every business table, so
a worker with no tenant context bound sees *nothing*: a sweep written the
obvious way would run happily and act on zero rows. The fix is not to give the
worker `BYPASSRLS` — that would be one broad grant to solve one narrow problem,
exempting every query the worker makes from every policy. Instead a locked-down
`SECURITY DEFINER` function returns the list of organization ids, and each
tenant's work runs with its own context bound, under RLS, exactly like a
request. See `app/db/sql_objects.py`.

Layout:

    queue.py        enqueue from request handlers; degrades when Redis is down
    context.py      session, tenant binding and storage for a running job
    dead_letter.py  what happens when a job exhausts its retries
    jobs/           the job functions themselves
    settings.py     the ARQ WorkerSettings the `arq` CLI loads
"""
