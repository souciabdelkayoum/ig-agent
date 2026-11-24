"""Instagram AI agent: reads the next due post from a Google Sheet (published as CSV),
asks Claude to write the caption, publishes it to Instagram, and logs it in posted.json.
Runs on GitHub Actions. Uses only the Python standard library."""
import csv, io, json, os, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

DUBAI = timezone(timedelta(hours=4))
SHEET_CSV_URL = os.environ["SHEET_CSV_URL"]
IG_USER_ID = os.environ.get("IG_USER_ID", "")
IG_TOKEN = os.environ.get("IG_TOKEN", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
REPO = os.environ.get("GITHUB_REPOSITORY", "")  # owner/repo, set by GitHub
API = "https://graph.instagram.com/v25.0"

BRAND = os.environ.get("BRAND") or (
    "Instagram account the_oasis_specialist.dubai: Abdul, Dubai real estate agent "
    "specialising in The Oasis by Emaar (ultra-luxury villas and mansions). Audience: "
    "entrepreneurs, CFOs, investors and decision makers. Tone: direct, expert, premium, "
    "no hype. Call to action: DM or WhatsApp +971 58 523 8800."
)
RULES = (
    "Rules: strong hook in the first line; 80-150 words; short paragraphs; end with a clear "
    "call to action; then 8-12 relevant hashtags. Never mention a villa/unit number or where "
    "a unit sits on the master plan. Never invent prices or facts: use only facts in the notes. "
    "Output ONLY the caption text."
)


def http(url, data=None, headers=None, method=None):
    body = None
    if data is not None:
        body = json.dumps(data).encode() if isinstance(data, dict) and headers else urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} from {url.split('?')[0]}: {e.read().decode()[:500]}")


def parse_date(s):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y",
                "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=DUBAI)
        except ValueError:
            pass
    return None


def image_link(value):
    value = value.strip()
    if value.startswith("http"):
        return value
    # a file name inside the repo's images/ folder (works when the repo is public)
    return f"https://raw.githubusercontent.com/{REPO}/main/images/{urllib.parse.quote(value)}"


def write_caption(row):
    if row.get("caption", "").strip():
        return row["caption"].strip()
    if DRY_RUN and not ANTHROPIC_KEY:
        return f"[dry-run caption for: {row['topic']}]"
    out = http(
        "https://api.anthropic.com/v1/messages",
        {
            "model": "claude-sonnet-5-5",
            "max_tokens": 1000,
            "system": "You write Instagram captions. " + BRAND + " " + RULES,
            "messages": [{"role": "user", "content": f"Topic: {row['topic']}\nNotes/facts: {row.get('notes') or 'none'}"}],
        },
        {"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    return json.loads(out)["content"][0]["text"].strip()


def publish(image_url, caption):
    c = json.loads(http(f"{API}/{IG_USER_ID}/media", {"image_url": image_url, "caption": caption, "access_token": IG_TOKEN}))
    cid = c["id"]
    for _ in range(12):  # wait up to ~2 minutes for Instagram to process the image
        s = json.loads(http(f"{API}/{cid}?fields=status_code&access_token={IG_TOKEN}"))
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") == "ERROR":
            sys.exit(f"Instagram could not process the image: {image_url}")
        time.sleep(10)
    p = json.loads(http(f"{API}/{IG_USER_ID}/media_publish", {"creation_id": cid, "access_token": IG_TOKEN}))
    return p["id"]


def main():
    rows = list(csv.DictReader(io.StringIO(http(SHEET_CSV_URL))))
    rows = [{k.strip().lower(): (v or "") for k, v in r.items() if k} for r in rows]
    posted = json.load(open("posted.json")) if os.path.exists("posted.json") else []
    done = {p["key"] for p in posted}
    now = datetime.now(DUBAI)

    due = []
    for r in rows:
        when = parse_date(r.get("date", ""))
        key = f"{r.get('date','').strip()}|{r.get('topic','').strip()}"
        if r.get("status", "").strip().lower() == "ready" and when and when <= now and key not in done and r.get("image", "").strip():
            due.append((when, key, r))
    if not due:
        print("Nothing due. Bye.")
        return
    when, key, row = sorted(due, key=lambda x: x[0])[0]  # one post per run
    caption = write_caption(row)
    img = image_link(row["image"])
    print(f"Posting '{row['topic']}' scheduled {when:%Y-%m-%d %H:%M}\nImage: {img}\n---\n{caption}\n---")
    post_id = "dry-run" if DRY_RUN else publish(img, caption)
    posted.append({"key": key, "post_id": post_id, "posted_at": now.isoformat(timespec="minutes"), "caption": caption})
    json.dump(posted, open("posted.json", "w"), indent=2, ensure_ascii=False)
    print(f"Done. Instagram post id: {post_id}")


if __name__ == "__main__":
    main()
