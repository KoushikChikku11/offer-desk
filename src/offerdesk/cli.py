"""Command line interface.

    offerdesk init                 create config files and the database
    offerdesk probe                check which ATS board slugs are live
    offerdesk sync [--firm X]      pull postings and score them
    offerdesk rescore              recompute scores after editing settings
    offerdesk queue [-n 15]        best open postings, highest score first
    offerdesk show <id>            full breakdown, resume pick, and letter draft
    offerdesk skip <id>            hide a posting from the queue
    offerdesk apply <id>           log that you applied (resume defaults to the router's pick)
    offerdesk add                  log an application from a site the tool does not source
    offerdesk log <app> <stage>    record oa / interview / final / offer / rejected / withdrawn
    offerdesk apps                 list applications and where each one stands
    offerdesk contact ...          add, list, update, or see follow ups due
    offerdesk stats                funnel, conversion by segment, and A vs B tests
    offerdesk demo                 build a synthetic demo database for the dashboard
"""

from __future__ import annotations

import argparse
import shutil
import sys
import textwrap
from pathlib import Path

from . import analytics, config, db, letters, network, pipeline, sources
from .models import ALL_STAGES, parse_ts, utcnow
from .router import router_from_db
from .scoring import explain

AMBER, DIM, BOLD, RESET = "\033[33m", "\033[2m", "\033[1m", "\033[0m"
if not sys.stdout.isatty():
    AMBER = DIM = BOLD = RESET = ""


def _db_path(args) -> Path:
    return Path(args.db) if args.db else config.DEFAULT_DB


def _row_id(uid: str) -> str:
    return uid.rsplit(":", 1)[-1]


def _need_posting(conn, key: str):
    row = db.get_posting(conn, key)
    if row is None:
        sys.exit(f"No unique posting matches {key!r}. Use the id shown in `offerdesk queue`.")
    return row


# Commands

def cmd_init(args):
    for name in ("settings", "profile"):
        real = config.CONFIG_DIR / f"{name}.yaml"
        if not real.exists():
            shutil.copy(config.CONFIG_DIR / f"{name}.example.yaml", real)
            print(f"created config/{name}.yaml")
        else:
            print(f"config/{name}.yaml already exists")
    (config.ROOT / "resumes").mkdir(exist_ok=True)
    with db.connect(_db_path(args)):
        pass
    print(f"database ready at {_db_path(args).relative_to(config.ROOT) if _db_path(args).is_relative_to(config.ROOT) else _db_path(args)}")
    print("next: edit config/profile.yaml in your own words, put your resumes in resumes/, then run `offerdesk probe`.")


def cmd_probe(args):
    firm_list = config.firms()
    sess = sources.session()
    changed = False
    for f in firm_list:
        if f.get("ats") not in sources.PROVIDERS:
            continue
        ok, n, msg = sources.probe(f, sess)
        mark = f"{AMBER}LIVE{RESET}" if ok else f"{DIM}dead{RESET}"
        print(f"  {mark}  {f['name']:<26} {f['ats']:<11} {f['slug']:<24} {n:>4} jobs  {msg if not ok else ''}")
        if not ok and not msg.startswith("HTTP"):
            continue  # network trouble on our side says nothing about the slug
        if f.get("verified") != ok:
            f["verified"] = ok
            changed = True
    if changed and not args.dry_run:
        config.save_firms(firm_list)
        print("updated verified flags in config/firms.yaml")


def cmd_sync(args):
    cfg = config.settings()
    firm_list = [f for f in config.firms() if f.get("verified", True) or args.include_unverified]
    with db.connect(_db_path(args)) as conn:
        rep = pipeline.sync(conn, firm_list, cfg, only=args.firm)
    print(f"{rep.firms_ok} boards synced, {rep.seen} postings seen, {rep.new} new, {rep.closed} closed")
    for name, err in rep.errors.items():
        print(f"  {DIM}error {name}: {err}{RESET}")
    if rep.new_top:
        print(f"\n{BOLD}New and worth a look{RESET}")
        for score, firm, title in rep.new_top[:10]:
            print(f"  {AMBER}{score:5.1f}{RESET}  {firm:<22} {title}")


def cmd_rescore(args):
    with db.connect(_db_path(args)) as conn:
        n = pipeline.rescore(conn, config.firms(), config.settings())
    print(f"rescored {n} active postings")


def cmd_queue(args):
    cfg = config.settings()
    min_score = cfg.get("scoring", {}).get("min_score_for_queue", 20) if not args.all else 0
    with db.connect(_db_path(args)) as conn:
        rows = db.queue(conn, limit=args.n, family=args.family, min_score=min_score)
    if not rows:
        print("Queue is empty. Run `offerdesk sync`, or `offerdesk queue --all` to lower the bar.")
        return
    print(f"{BOLD}{'SCORE':>5}  {'ID':<12} {'FIRM':<22} {'FAMILY':<18} TITLE{RESET}")
    for r in rows:
        print(f"{AMBER}{r['score']:5.1f}{RESET}  {_row_id(r['uid']):<12} {r['firm'][:21]:<22} "
              f"{(r['family'] or '')[:17]:<18} {r['title'][:70]}")


def cmd_show(args):
    import json

    cfg = config.settings()
    with db.connect(_db_path(args)) as conn:
        r = _need_posting(conn, args.id)
        router = router_from_db(conn, cfg.get("analytics", {}).get("min_labels_for_classifier", 20))
        route = router.route(f"{r['title']} {r['description'] or ''}", r["family"] or "other")
        firm_contacts = db.contacts(conn, r["firm"])
    detail = json.loads(r["score_detail"] or "{}")
    print(f"{BOLD}{r['firm']}  |  {r['title']}{RESET}")
    print(f"{r['location'] or ''}  {r['url'] or ''}")
    print(f"\nscore {AMBER}{r['score']}{RESET}   {explain(detail)}")
    print(f"resume  {AMBER}{route.version}{RESET}   {route.summary()}")
    if firm_contacts:
        print("contacts here: " + ", ".join(f"{c['name']} ({c['status']})" for c in firm_contacts))
    else:
        print(f"{DIM}no contacts at {r['firm']} yet. A referral is worth more than any resume tweak.{RESET}")
    if args.letter:
        text = letters.draft(config.profile(), r["firm"], r["title"], r["family"] or "other", route.version)
        print(f"\n{BOLD}Draft letter ({letters.word_count(text)} words){RESET}\n{text}")
    if args.description and r["description"]:
        print("\n" + textwrap.fill(r["description"][:2500], 100))


def cmd_skip(args):
    with db.connect(_db_path(args)) as conn:
        r = _need_posting(conn, args.id)
        db.set_posting_status(conn, r["uid"], "skipped")
    print(f"skipped {r['firm']} {r['title']}")


def cmd_apply(args):
    cfg = config.settings()
    with db.connect(_db_path(args)) as conn:
        r = _need_posting(conn, args.id)
        router = router_from_db(conn, cfg.get("analytics", {}).get("min_labels_for_classifier", 20))
        route = router.route(f"{r['title']} {r['description'] or ''}", r["family"] or "other")
        version = (args.resume or route.version).upper()
        applied = parse_ts(args.date) if args.date else utcnow()
        posted = parse_ts(r["published_at"]) or parse_ts(r["first_seen"])
        lag = round((applied - posted).total_seconds() / 86400, 1) if posted else None
        letter = letters.draft(config.profile(), r["firm"], r["title"], r["family"] or "other", version) if args.letter else None
        app_id = db.create_application(
            conn, posting_uid=r["uid"], firm=r["firm"], title=r["title"], family=r["family"],
            resume_version=version, routed_version=route.version, route_confidence=route.confidence,
            referral_contact_id=args.referral, applied_at=applied.isoformat(),
            days_after_posting=lag, letter=letter, notes=args.note,
        )
    flag = "" if version == route.version else f"  {DIM}(router suggested {route.version}; logged as an override){RESET}"
    print(f"application #{app_id}: {r['firm']} {r['title']} with {version}{flag}")


def cmd_add(args):
    from .scoring import classify_family

    family = args.family or classify_family(args.title)
    with db.connect(_db_path(args)) as conn:
        router = router_from_db(conn)
        route = router.route(args.title, family)
        version = (args.resume or route.version).upper()
        app_id = db.create_application(
            conn, firm=args.firm, title=args.title, family=family, resume_version=version,
            routed_version=route.version, route_confidence=route.confidence,
            referral_contact_id=args.referral,
            applied_at=(parse_ts(args.date) or utcnow()).isoformat(),
            days_after_posting=args.days_after_posting, notes=args.note,
        )
    print(f"application #{app_id}: {args.firm} {args.title} ({family}) with {version}")


def cmd_log(args):
    with db.connect(_db_path(args)) as conn:
        app = conn.execute("SELECT * FROM applications WHERE id=?", (args.app,)).fetchone()
        if not app:
            sys.exit(f"No application #{args.app}")
        db.add_event(conn, args.app, args.stage, (parse_ts(args.date) or utcnow()).isoformat(), args.note or "")
    print(f"#{args.app} {app['firm']}: {args.stage}")


def cmd_apps(args):
    cfg = config.settings()
    with db.connect(_db_path(args)) as conn:
        apps, events = db.applications_frame(conn)
    if apps.empty:
        print("No applications logged yet.")
        return
    df = analytics.build_outcomes(apps, events, cfg.get("analytics", {}).get("ghost_after_days", 30))
    df = df.sort_values("applied_at", ascending=False).head(args.n)
    print(f"{BOLD}{'#':>4}  {'APPLIED':<10} {'FIRM':<22} {'RESUME':<6} {'STATUS':<10} TITLE{RESET}")
    for _, r in df.iterrows():
        print(f"{r['id']:>4}  {r['applied_at']:%Y-%m-%d} {r['firm'][:21]:<22} {r['resume_version']:<6} "
              f"{AMBER}{r['status']:<10}{RESET} {r['title'][:60]}")


def cmd_contact(args):
    with db.connect(_db_path(args)) as conn:
        if args.action == "add":
            cid = db.add_contact(conn, name=args.name, firm=args.firm, role=args.role,
                                 relationship=args.relationship, status=args.status, notes=args.note)
            print(f"contact #{cid} added")
        elif args.action == "update":
            fields = {"last_contact": utcnow().isoformat()}
            if args.status:
                fields["status"] = args.status
            if args.note:
                fields["notes"] = args.note
            db.update_contact(conn, args.id, **fields)
            print(f"contact #{args.id} updated")
        elif args.action == "due":
            due = network.due_followups(conn, args.days)
            if not due:
                print("No follow ups due.")
            for c in due:
                print(f"  #{c['id']:<3} {c['name']:<20} {c['firm']:<22} {c['status']:<12} quiet {c['days_quiet']}d")
        elif args.action == "coverage":
            for c in network.firm_coverage(conn):
                mark = AMBER if not c["contacts"] else ""
                print(f"  {mark}{c['firm']:<26}{RESET} contacts {c['contacts']}  referrals {c['referrals'] or 0}")
        else:
            for c in db.contacts(conn, args.firm):
                print(f"  #{c['id']:<3} {c['name']:<20} {c['firm']:<22} {c['status']:<12} {c['relationship'] or ''}")


def cmd_stats(args):
    cfg = config.settings()
    with db.connect(_db_path(args)) as conn:
        apps, events = db.applications_frame(conn)
    if apps.empty:
        print("No applications logged yet. Try `offerdesk demo` then `offerdesk --db data/demo.db stats`.")
        return
    df = analytics.build_outcomes(apps, events, cfg.get("analytics", {}).get("ghost_after_days", 30))
    metric = args.metric
    print(f"{BOLD}{len(df)} applications.  Metric: {analytics.METRICS[metric]}{RESET}\n")

    print(f"{BOLD}Funnel{RESET}")
    for _, r in analytics.funnel(df).iterrows():
        print(f"  {r['stage']:<10} {r['count']:>4}  {r['rate']:6.1%}   95% CI [{r['lo']:.1%}, {r['hi']:.1%}]")

    for col, label in (("resume_version", "Resume"), ("family", "Role family"),
                       ("referred", "Referral"), ("apply_lag", "Days after posting")):
        print(f"\n{BOLD}{label}{RESET}")
        for _, r in analytics.rate_table(df, col, metric).iterrows():
            print(f"  {str(r[col]):<20} {r['k']:>3}/{r['n']:<4} {r['rate']:6.1%}   [{r['lo']:.1%}, {r['hi']:.1%}]")

    audit = analytics.router_audit(df, metric)
    if not audit.empty:
        print(f"\n{BOLD}Router audit{RESET}")
        for _, r in audit.iterrows():
            print(f"  {r['choice']:<20} {r['k']:>3}/{r['n']:<4} {r['rate']:6.1%}   [{r['lo']:.1%}, {r['hi']:.1%}]")

    print(f"\n{BOLD}Tests{RESET}")
    print("  " + analytics.describe_comparison(analytics.compare(df, "resume_version", "CSQNT", "FIN", metric)))
    print("  " + analytics.describe_comparison(analytics.compare(df, "referred", True, False, metric)))

    km = analytics.kaplan_meier(df["response_days"], df["replied"])
    med = analytics.median_survival(km)
    print(f"\n{BOLD}Time to first reply{RESET}  (Kaplan Meier, silent applications censored)")
    print(f"  median {med:.0f} days" if med is not None else "  fewer than half have replied so far; median not reached")


def cmd_demo(args):
    from . import demo

    path = Path(args.db) if args.db else config.DATA_DIR / "demo.db"
    demo.seed(path, n_apps=args.n)
    print(f"synthetic demo data written to {path}")
    print(f"try: offerdesk --db {path} stats   or   streamlit run app.py -- --db {path}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="offerdesk", description="Run your recruiting like a trading desk.")
    p.add_argument("--db", help="path to the SQLite database (default data/offerdesk.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    s = sub.add_parser("probe")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("sync")
    s.add_argument("--firm")
    s.add_argument("--include-unverified", action="store_true")
    s.set_defaults(func=cmd_sync)

    sub.add_parser("rescore").set_defaults(func=cmd_rescore)

    s = sub.add_parser("queue")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--family")
    s.add_argument("--all", action="store_true", help="ignore the minimum score")
    s.set_defaults(func=cmd_queue)

    s = sub.add_parser("show")
    s.add_argument("id")
    s.add_argument("--letter", action="store_true")
    s.add_argument("--description", action="store_true")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("skip")
    s.add_argument("id")
    s.set_defaults(func=cmd_skip)

    s = sub.add_parser("apply")
    s.add_argument("id")
    s.add_argument("--resume", choices=["FIN", "CSQNT", "fin", "csqnt"])
    s.add_argument("--referral", type=int, help="contact id who referred you")
    s.add_argument("--date", help="ISO date if you applied earlier")
    s.add_argument("--letter", action="store_true", help="store the drafted letter with the application")
    s.add_argument("--note")
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("add")
    s.add_argument("--firm", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--family")
    s.add_argument("--resume", choices=["FIN", "CSQNT", "fin", "csqnt"])
    s.add_argument("--referral", type=int)
    s.add_argument("--date")
    s.add_argument("--days-after-posting", type=float)
    s.add_argument("--note")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("log")
    s.add_argument("app", type=int)
    s.add_argument("stage", choices=[x for x in ALL_STAGES if x != "applied"])
    s.add_argument("--date")
    s.add_argument("--note")
    s.set_defaults(func=cmd_log)

    s = sub.add_parser("apps")
    s.add_argument("-n", type=int, default=30)
    s.set_defaults(func=cmd_apps)

    s = sub.add_parser("contact")
    s.add_argument("action", choices=["add", "list", "update", "due", "coverage"])
    s.add_argument("--id", type=int)
    s.add_argument("--name")
    s.add_argument("--firm")
    s.add_argument("--role")
    s.add_argument("--relationship", choices=["alum", "club", "recruiter", "friend", "cold"])
    s.add_argument("--status", choices=list(network.STATUSES))
    s.add_argument("--note")
    s.add_argument("--days", type=int, default=10)
    s.set_defaults(func=cmd_contact)

    s = sub.add_parser("stats")
    s.add_argument("--metric", choices=list(analytics.METRICS), default="advanced")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("demo")
    s.add_argument("-n", type=int, default=140)
    s.set_defaults(func=cmd_demo)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "contact" and args.action == "add" and not (args.name and args.firm):
        sys.exit("contact add needs --name and --firm")
    if args.cmd == "contact" and args.action == "update" and not args.id:
        sys.exit("contact update needs --id")
    if args.cmd == "contact" and args.action == "add" and not args.status:
        args.status = "reached_out"
    args.func(args)


if __name__ == "__main__":
    main()
