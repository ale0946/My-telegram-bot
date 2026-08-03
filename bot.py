
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is missing from GitHub Secrets/.env"
    )

if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY is missing from GitHub Secrets/.env"
    )


client = Groq(api_key=GROQ_API_KEY)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("LiverpoolNewsBot")


# =========================================================
# TRUSTED SOURCES
# =========================================================

TRUSTED_DOMAINS = {
    "liverpoolfc.com": "Liverpool FC",
    "theathletic.com": "The Athletic",
    "thetimes.com": "The Times",
    "x.com": "Fabrizio Romano",
    "twitter.com": "Fabrizio Romano",
    "fabricioromano.com": "Fabrizio Romano",
}


# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS posted_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT UNIQUE,
            title TEXT NOT NULL,
            url TEXT,
            source TEXT,
            image_hash TEXT,
            posted_at INTEGER NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS used_images (
            image_hash TEXT PRIMARY KEY,
            image_url TEXT,
            used_at INTEGER NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    return conn


# =========================================================
# TEXT HELPERS
# =========================================================

def clean_text(text):
    if not text:
        return ""

    text = BeautifulSoup(
        str(text),
        "html.parser"
    ).get_text(" ", strip=True)

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def normalize(text):
    text = clean_text(text).lower()
    text = re.sub(
        r"[^a-z0-9\u1200-\u137f\s]",
        " ",
        text
    )
    text = re.sub(
        r"\s+",
        " ",
        text
    )
    return text.strip()


def make_hash(text):
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def title_similarity(a, b):
    a_words = set(normalize(a).split())
    b_words = set(normalize(b).split())

    if not a_words or not b_words:
        return 0.0

    return len(
        a_words.intersection(b_words)
    ) / len(
        a_words.union(b_words)
    )


# =========================================================
# LIVERPOOL RELEVANCE
# =========================================================

LIVERPOOL_KEYWORDS = [
    "liverpool",
    "liverpool fc",
    "lfc",
    "anfield",
    "reds",
    "arne slot",
    "slot",
    "salah",
    "mohamed salah",
    "gakpo",
    "cody gakpo",
    "diaz",
    "luis diaz",
    "nunez",
    "darwin nunez",
    "szoboszlai",
    "mac allister",
    "gravenberch",
    "wirtz",
    "frimpong",
    "van dijk",
    "konate",
    "alisson",
    "robertson",
    "alexander-arnold",
    "alexander arnold",
    "bradley",
    "elliott",
    "jones",
    "chiesa",
    "endo",
    "iraola",
]


def is_liverpool_related(text):
    text = text.lower()

    return any(
        keyword in text
        for keyword in LIVERPOOL_KEYWORDS
    )


# =========================================================
# SOURCE / DOMAIN
# =========================================================

def get_domain(url):
    try:
        host = urlparse(url).netloc.lower()
        host = host.replace(
            "www.",
            ""
        )
        return host
    except Exception:
        return ""


def trusted_source(url):
    domain = get_domain(url)

    for trusted_domain, name in TRUSTED_DOMAINS.items():

        if (
            domain == trusted_domain
            or domain.endswith(
                "." + trusted_domain
            )
        ):
            return name

    return None


# =========================================================
# TELEGRAM API
# =========================================================

def telegram_api(method, data=None, files=None):

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/{method}"
    )

    try:

        response = requests.post(
            url,
            data=data,
            files=files,
            timeout=40
        )

        try:
            result = response.json()
        except Exception:
            result = {
                "ok": False,
                "description": response.text
            }

        if not result.get("ok"):
            logger.error(
                "Telegram API error: %s",
                result
            )

        return result

    except Exception as e:

        logger.error(
            "Telegram request failed: %s",
            e
        )

        return {
            "ok": False,
            "description": str(e)
        }


def telegram_send_message(text):

    result = telegram_api(
        "sendMessage",
        data={
            "chat_id": CHANNEL,
            "text": text,
            "disable_web_page_preview": True
        }
    )

    return result.get("ok", False)


def telegram_send_photo(
    image_bytes,
    caption
):

    result = telegram_api(
        "sendPhoto",
        data={
            "chat_id": CHANNEL,
            "caption": caption
        },
        files={
            "photo": (
                "liverpool_news.jpg",
                image_bytes,
                "image/jpeg"
            )
        }
    )

    return result.get("ok", False)


def telegram_startup_test():

    message = (
        "🤖 Liverpool ዜና Bot ተነስቷል 🚀\n\n"
        "የዜና ፍለጋ፣ የአማርኛ አዘጋጅ፣ "
        "የምስል ማጣሪያ እና duplicate መከላከያ "
        "እየሰሩ ናቸው።"
    )

    return telegram_send_message(
        message
    )


# =========================================================
# RESOLVE URL
# =========================================================

def resolve_url(url):

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=25,
            allow_redirects=True
        )

        final_url = response.url

        logger.info(
            "URL resolved: %s -> %s",
            url,
            final_url
        )

        return final_url

    except Exception as e:

        logger.warning(
            "URL resolution failed: %s",
            e
        )

        return url


# =========================================================
# GOOGLE NEWS RSS
# =========================================================

def get_google_news():

    queries = [
        "Liverpool FC",
        "Liverpool transfer",
        "Liverpool signing",
        "Liverpool contract",
        "Liverpool injury",
        "Liverpool manager",
        "Liverpool player",
    ]

    results = []

    for query in queries:

        rss_url = (
            "https://news.google.com/rss/search?"
            f"q={quote_plus(query)}"
            "&hl=en-US"
            "&gl=GB"
            "&ceid=GB:en"
        )

        try:

            response = requests.get(
                rss_url,
                headers=HEADERS,
                timeout=25
            )

            response.raise_for_status()

            feed = feedparser.parse(
                response.content
            )

            for entry in feed.entries:

                title = clean_text(
                    getattr(
                        entry,
                        "title",
                        ""
                    )
                )

                link = getattr(
                    entry,
                    "link",
                    ""
                )

                if not title or not link:
                    continue

                source_title = ""

                source_obj = getattr(
                    entry,
                    "source",
                    None
                )

                if source_obj:
                    source_title = clean_text(
                        getattr(
                            source_obj,
                            "title",
                            ""
                        )
                    )

                published = getattr(
                    entry,
                    "published_parsed",
                    None
                )

                results.append({
                    "title": title,
                    "url": link,
                    "source_title": source_title,
                    "published": published
                })

        except Exception as e:

            logger.warning(
                "Google News error [%s]: %s",
                query,
                e
            )

    # Remove exact duplicates
    unique = {}

    for item in results:

        key = (
            normalize(item["title"])
            + "|"
            + item["url"]
        )

        unique[key] = item

    return list(
        unique.values()
    )


# =========================================================
# ARTICLE FETCH
# =========================================================

def extract_meta(
    soup,
    property_name=None,
    name=None
):

    tag = None

    if property_name:
        tag = soup.find(
            "meta",
            attrs={
                "property": property_name
            }
        )

    if not tag and name:
        tag = soup.find(
            "meta",
            attrs={
                "name": name
            }
        )

    if tag:
        return clean_text(
            tag.get(
                "content",
                ""
            )
        )

    return ""


def extract_article_image(
    soup,
    page_url
):

    candidates = []

    # OpenGraph
    for prop in [
        "og:image",
        "og:image:url"
    ]:

        value = extract_meta(
            soup,
            property_name=prop
        )

        if value:
            candidates.append(value)

    # Twitter
    twitter_image = extract_meta(
        soup,
        name="twitter:image"
    )

    if twitter_image:
        candidates.append(
            twitter_image
        )

    # Article images
    article = soup.find("article")

    if article:

        for img in article.find_all("img"):

            src = (
                img.get("src")
                or img.get("data-src")
                or img.get("data-lazy-src")
                or img.get("data-original")
            )

            if src:
                candidates.append(src)

    # All images fallback
    for img in soup.find_all("img"):

        src = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-lazy-src")
        )

        if src:
            candidates.append(src)

    for image in candidates:

        image = image.strip()

        if not image:
            continue

        if image.startswith(
            "data:"
        ):
            continue

        image = urljoin(
            page_url,
            image
        )

        low = image.lower()

        if any(
            ext in low
            for ext in [
                ".jpg",
                ".jpeg",
                ".png",
                ".webp"
            ]
        ):
            return image

        # Some sites don't expose extensions
        if image.startswith(
            "http://"
        ) or image.startswith(
            "https://"
        ):
            return image

    return None


def fetch_article(url):

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30,
            allow_redirects=True
        )

        if response.status_code != 200:
            logger.warning(
                "Article HTTP %s: %s",
                response.status_code,
                url
            )
            return None

        final_url = response.url

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # Save metadata BEFORE removing tags
        meta_title = extract_meta(
            soup,
            property_name="og:title"
        )

        meta_description = extract_meta(
            soup,
            property_name="og:description"
        )

        image_url = extract_article_image(
            soup,
            final_url
        )

        # Remove unwanted parts
        for tag in soup.find_all([
            "script",
            "style",
            "noscript",
            "svg",
            "iframe",
            "nav",
            "footer",
            "header",
            "form"
        ]):
            tag.decompose()

        title = meta_title

        if not title and soup.title:
            title = soup.title.get_text(
                " ",
                strip=True
            )

        article = soup.find(
            "article"
        )

        if article:

            paragraphs = [
                p.get_text(
                    " ",
                    strip=True
                )
                for p in article.find_all("p")
            ]

        else:

            paragraphs = [
                p.get_text(
                    " ",
                    strip=True
                )
                for p in soup.find_all("p")
            ]

        paragraphs = [
            clean_text(p)
            for p in paragraphs
            if len(
                clean_text(p)
            ) >= 25
        ]

        body = "\n".join(
            paragraphs
        )

        if len(body) < 500:
            body += "\n" + meta_description

        body = clean_text(body)

        if len(body) > 14000:
            body = body[:14000]

        return {
            "title": clean_text(title),
            "body": body,
            "image_url": image_url,
            "url": final_url,
        }

    except Exception as e:

        logger.warning(
            "Article fetch error: %s",
            e
        )

        return None


# =========================================================
# IMAGE DOWNLOAD
# =========================================================

def download_image(image_url):

    if not image_url:
        return None

    try:

        response = requests.get(
            image_url,
            headers={
                **HEADERS,
                "Accept": "image/avif,image/webp,"
                          "image/apng,image/svg+xml,"
                          "image/*,*/*;q=0.8"
            },
            timeout=25,
            stream=True
        )

        if response.status_code != 200:
            return None

        content_type = response.headers.get(
            "Content-Type",
            ""
        ).lower()

        data = response.content

        if not data:
            return None

        if len(data) < 10_000:
            return None

        if len(data) > 15 * 1024 * 1024:
            return None

        # Accept image content types
        # Some websites incorrectly label images,
        # so don't reject solely on missing type.
        if (
            content_type
            and not content_type.startswith("image/")
        ):
            logger.info(
                "Not an image: %s",
                content_type
            )
            return None

        image_hash = hashlib.sha256(
            data
        ).hexdigest()

        return {
            "bytes": data,
            "hash": image_hash,
            "url": image_url,
            "content_type": content_type
        }

    except Exception as e:

        logger.warning(
            "Image download error: %s",
            e
        )

        return None


# =========================================================
# IMAGE DUPLICATE CHECK
# =========================================================

def image_was_used(image_hash):

    conn = get_db()

    row = conn.execute(
        """
        SELECT image_hash
        FROM used_images
        WHERE image_hash = ?
        LIMIT 1
        """,
        (image_hash,)
    ).fetchone()

    conn.close()

    return row is not None


def save_image(image_hash, image_url):

    conn = get_db()

    conn.execute(
        """
        INSERT OR IGNORE INTO used_images
        (
            image_hash,
            image_url,
            used_at
        )
        VALUES (?, ?, ?)
        """,
        (
            image_hash,
            image_url,
            int(time.time())
        )
    )

    conn.commit()
    conn.close()


# =========================================================
# NEWS DUPLICATE CHECK
# =========================================================

def news_was_posted(title, url):

    fp = make_hash(
        normalize(title)
        + "|"
        + normalize(url)
    )

    conn = get_db()

    row = conn.execute(
        """
        SELECT id
        FROM posted_news
        WHERE fingerprint = ?
        LIMIT 1
        """,
        (fp,)
    ).fetchone()

    if row:
        conn.close()
        return True

    recent = conn.execute(
        """
        SELECT title
        FROM posted_news
        ORDER BY posted_at DESC
        LIMIT 150
        """
    ).fetchall()

    conn.close()

    for old in recent:

        old_title = old[0]

        if title_similarity(
            title,
            old_title
        ) >= 0.70:

            return True

    return False


def save_post(
    title,
    url,
    source,
    image_hash=""
):

    fp = make_hash(
        normalize(title)
        + "|"
        + normalize(url)
    )

    conn = get_db()

    conn.execute(
        """
        INSERT OR IGNORE INTO posted_news
        (
            fingerprint,
            title,
            url,
            source,
            image_hash,
            posted_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            fp,
            title,
            url,
            source,
            image_hash,
            int(time.time())
        )
    )

    conn.commit()
    conn.close()


# =========================================================
# 5 MINUTE GAP
# =========================================================

def can_post():

    conn = get_db()

    row = conn.execute(
        """
        SELECT posted_at
        FROM posted_news
        ORDER BY posted_at DESC
        LIMIT 1
        """
    ).fetchone()

    conn.close()

    if not row:
        return True

    elapsed = (
        time.time()
        - int(row[0])
    )

    if elapsed >= MIN_POST_GAP:
        return True

    remaining = int(
        MIN_POST_GAP - elapsed
    )

    logger.info(
        "5-minute gap active. "
        "%s seconds remaining.",
        remaining
    )

    return False


# =========================================================
# ARTICLE AGE
# =========================================================

def is_recent(entry):

    published = entry.get(
        "published"
    )

    if not published:
        return True

    try:

        timestamp = time.mktime(
            published
        )

        published_dt = datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc
        )

        age = (
            datetime.now(timezone.utc)
            - published_dt
        )

        if age > timedelta(
            hours=MAX_ARTICLE_AGE_HOURS
        ):
            return False

        return True

    except Exception:
        return True


# =========================================================
# AI NEWS EDITOR
# =========================================================

NEWS_EDITOR_PROMPT = """
አንተ ለኢትዮጵያዊ የLiverpool FC Telegram ቻናል
የምትሰራ ከፍተኛ የስፖርት አርታኢ ነህ።

የተሰጠህን English article በቃል በቃል አትተርጉም።
መጀመሪያ ዜናውን ተረዳ፣ ከዚያ በተፈጥሯዊ፣
በሚነበብ እና በሙያዊ የአማርኛ የእግር ኳስ
ዘገባ መልክ አዘጋጀው።

ጥብቅ ደንቦች:

1. በarticle ውስጥ የሌለ እውነታ አትጨምር።
2. Quote አትፍጠር።
3. Transfer fee አትፍጠር።
4. Contract length አትፍጠር።
5. Injury አትፍጠር።
6. Date አትፍጠር።
7. Source አትፍጠር።
8. "could", "may", "reportedly",
   "understood", "according to" ያሉ ጥርጣሬዎችን
   እንደ እርግጠኛ እውነታ አትቀይር።
9. ዜናው Liverpool FC ጋር በግልጽ መያያዝ አለበት።
10. English headline አትጻፍ።
11. English paragraph አትጻፍ።
12. የቃል በቃል machine translation አትስራ።
13. የኢትዮጵያ የስፖርት ዘገባ ቋንቋ ተጠቀም።
14. የተጫዋች ስሞችን በትክክል ጠብቅ።
15. Headline አጭር፣ ግልጽ እና የዜና አይነት ይሁን።
16. Body 2-4 አጭር አንቀጾች ይሁን።
17. Hashtag አትጨምር።
18. Emoji አትጨምር።
19. Markdown አትጠቀም።
20. @yegnaLiverpoolET አትጨምር።
21. በarticle ውስጥ በቂ መረጃ ከሌለ REJECT አድርግ።
22. የተሰጠው article በግልጽ Liverpool ጋር
    ካልተያያዘ REJECT አድርግ።

JSON ብቻ መልስ:

{
  "decision": "POST" or "REJECT",
  "headline": "...",
  "body": "...",
  "confidence": 0-100,
  "reason": "..."
}
"""


def ai_edit_news(
    title,
    body,
    source,
    url
):

    prompt = f"""
SOURCE:
{source}

TITLE:
{title}

ARTICLE:
{body}

URL:
{url}
"""

    try:

        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=0.15,
            max_tokens=1600,
            response_format={
                "type": "json_object"
            },
            messages=[
                {
                    "role": "system",
                    "content": NEWS_EDITOR_PROMPT
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        raw = (
            completion
            .choices[0]
            .message
            .content
        )

        result = json.loads(raw)

        return result

    except Exception as e:

        logger.error(
            "Groq editor error: %s",
            e
        )

        return None


# =========================================================
# AMHARIC QUALITY CHECK
# =========================================================

def amharic_ratio(text):

    if not text:
        return 0

    chars = re.findall(
        r"[\u1200-\u137F]",
        text
    )

    letters = re.findall(
        r"[A-Za-z\u1200-\u137F]",
        text
    )

    if not letters:
        return 0

    return len(chars) / len(letters)


def english_sentence_detected(text):

    english_words = re.findall(
        r"\b(the|this|that|with|from|will|has|have|"
        r"are|was|were|according|reportedly|"
        r"could|would|should|for|and|but)\b",
        text.lower()
    )

    return len(english_words) >= 4


def valid_amharic_output(
    headline,
    body
):

    if not headline or not body:
        return False

    if amharic_ratio(
        headline + " " + body
    ) < 0.30:
        return False

    if english_sentence_detected(
        headline + " " + body
    ):
        return False

    return True


# =========================================================
# IMAGE RELEVANCE
# =========================================================

def image_relevance_score(
    image_url,
    article_title,
    article_body
):

    """
    Conservative metadata-based image check.

    We don't pretend that a text-only LLM can see the
    downloaded image. Instead we inspect the URL and
    page metadata and only use images from the article
    itself.
    """

    if not image_url:
        return 0

    text = (
        article_title
        + " "
        + article_body
        + " "
        + image_url
    ).lower()

    score = 0

    names = [
        "salah",
        "gakpo",
        "cody-gakpo",
        "diaz",
        "nunez",
        "darwin",
        "szoboszlai",
        "mac-allister",
        "gravenberch",
        "wirtz",
        "frimpong",
        "van-dijk",
        "konate",
        "alisson",
        "robertson",
        "alexander-arnold",
        "bradley",
        "elliott",
        "chiesa",
        "slot",
        "iraola",
        "liverpool",
        "anfield",
    ]

    found = 0

    for name in names:

        normalized_name = name.replace(
            "-",
            " "
        )

        if (
            name in text
            or normalized_name in text
        ):
            found += 1

    if found >= 1:
        score += 50

    if "liverpool" in text:
        score += 25

    if any(
        ext in image_url.lower()
        for ext in [
            ".jpg",
            ".jpeg",
            ".png",
            ".webp"
        ]
    ):
        score += 10

    return min(
        score,
        100
    )


# =========================================================
# CAPTION
# =========================================================

def make_caption(
    headline,
    body
):

    caption = (
        f"{headline}\n\n"
        f"{body}\n\n"
        f"@yegnaLiverpoolET"
    )

    # Telegram photo caption limit
    if len(caption) > 1020:
        caption = caption[:1017] + "..."

    return caption


# =========================================================
# PROCESS ONE NEWS ITEM
# =========================================================

def process_news(entry):

    original_title = clean_text(
        entry.get(
            "title",
            ""
        )
    )

    google_url = entry.get(
        "url",
        ""
    )

    if not original_title or not google_url:
        return False

    logger.info(
        "------------------------------------------"
    )

    logger.info(
        "Checking: %s",
        original_title
    )

    # -----------------------------------------------------
    # Recent
    # -----------------------------------------------------

    if not is_recent(entry):

        logger.info(
            "REJECT: article too old"
        )

        return False

    # -----------------------------------------------------
    # Resolve Google News URL FIRST
    # -----------------------------------------------------

    real_url = resolve_url(
        google_url
    )

    # -----------------------------------------------------
    # Trusted source AFTER redirect
    # -----------------------------------------------------

    source = trusted_source(
        real_url
    )

    if not source:

        logger.info(
            "REJECT: untrusted source"
        )

        return False

    logger.info(
        "Trusted source: %s",
        source
    )

    # -----------------------------------------------------
    # Quick Liverpool filter
    # -----------------------------------------------------

    if not is_liverpool_related(
        original_title
    ):

        logger.info(
            "REJECT: title not Liverpool related"
        )

        return False

    # -----------------------------------------------------
    # Fetch article
    # -----------------------------------------------------

    article = fetch_article(
        real_url
    )

    if not article:

        logger.info(
            "REJECT: article unavailable"
        )

        return False

    title = (
        article["title"]
        or original_title
    )

    body = article["body"]

    if len(body) < 250:

        logger.info(
            "REJECT: article body too short"
        )

        return False

    # -----------------------------------------------------
    # Full Liverpool check
    # -----------------------------------------------------

    combined = (
        title
        + " "
        + body
    )

    if not is_liverpool_related(
        combined
    ):

        logger.info(
            "REJECT: full article not Liverpool"
        )

        return False

    # -----------------------------------------------------
    # Duplicate
    # -----------------------------------------------------

    if news_was_posted(
        title,
        real_url
    ):

        logger.info(
            "REJECT: duplicate news"
        )

        return False

    # -----------------------------------------------------
    # 5-minute rule
    # -----------------------------------------------------

    if not can_post():

        logger.info(
            "WAIT: previous post was "
            "less than 5 minutes ago"
        )

        return False

    # -----------------------------------------------------
    # AI editor
    # -----------------------------------------------------

    logger.info(
        "AI is editing the article..."
    )

    edited = ai_edit_news(
        title,
        body,
        source,
        real_url
    )

    if not edited:

        logger.info(
            "REJECT: AI returned nothing"
        )

        return False

    if edited.get(
        "decision"
    ) != "POST":

        logger.info(
            "REJECT: AI: %s",
            edited.get(
                "reason",
                ""
            )
        )

        return False

    confidence = int(
        edited.get(
            "confidence",
            0
        )
    )

    if confidence < 85:

        logger.info(
            "REJECT: AI confidence %s",
            confidence
        )

        return False

    headline = clean_text(
        edited.get(
            "headline",
            ""
        )
    )

    body_am = clean_text(
        edited.get(
            "body",
            ""
        )
    )

    if not valid_amharic_output(
        headline,
        body_am
    ):

        logger.info(
            "REJECT: poor Amharic output"
        )

        return False

    # -----------------------------------------------------
    # Image
    # -----------------------------------------------------

    image = None

    image_url = article.get(
        "image_url"
    )

    if image_url:

        logger.info(
            "Article image found."
        )

        score = image_relevance_score(
            image_url,
            title,
            body
        )

        logger.info(
            "Image relevance score: %s",
            score
        )

        downloaded = download_image(
            image_url
        )

        if downloaded:

            if image_was_used(
                downloaded["hash"]
            ):

                logger.info(
                    "Image already used. "
                    "Trying no image."
                )

            else:

                image = downloaded

    # -----------------------------------------------------
    # Build Telegram post
    # -----------------------------------------------------

    caption = make_caption(
        headline,
        body_am
    )

    # -----------------------------------------------------
    # Send photo if valid
    # -----------------------------------------------------

    if image:

        logger.info(
            "Sending NEWS + IMAGE to Telegram..."
        )

        success = telegram_send_photo(
            image["bytes"],
            caption
        )

        if not success:

            logger.error(
                "Photo failed. "
                "Trying text message..."
            )

            success = telegram_send_message(
                caption
            )

        if not success:

            logger.error(
                "Telegram POST FAILED."
            )

            return False

        save_image(
            image["hash"],
            image["url"]
        )

        image_hash = image["hash"]

    else:

        logger.info(
            "No safe unique image. "
            "Sending text news."
        )

        success = telegram_send_message(
            caption
        )

        if not success:

            logger.error(
                "Telegram text POST FAILED."
            )

            return False

        image_hash = ""

    # -----------------------------------------------------
    # Save successfully posted news
    # -----------------------------------------------------

    save_post(
        headline,
        real_url,
        source,
        image_hash
    )

    logger.info(
        "SUCCESS: NEWS POSTED TO TELEGRAM ✅"
    )

    return True


# =========================================================
# MAIN LOOP
# =========================================================

def run_bot():

    logger.info(
        "=========================================="
    )

    logger.info(
        "Liverpool Telegram News Bot"
    )

    logger.info(
        "Starting..."
    )

    logger.info(
        "Channel: %s",
        CHANNEL
    )

    logger.info(
        "Check interval: 5 minutes"
    )

    logger.info(
        "Minimum post gap: 5 minutes"
    )

    logger.info(
        "=========================================="
    )

    # Initialize database
    get_db().close()

    # -----------------------------------------------------
    # Telegram connection test
    # -----------------------------------------------------

    logger.info(
        "Testing Telegram connection..."
    )

    me = telegram_api(
        "getMe"
    )

    if not me.get("ok"):

        raise RuntimeError(
            "Telegram bot connection failed: "
            + str(me)
        )

    bot_name = me["result"].get(
        "username",
        "unknown"
    )

    logger.info(
        "Telegram bot connected: @%s",
        bot_name
    )

    # -----------------------------------------------------
    # Startup message
    # -----------------------------------------------------

    if SEND_STARTUP_TEST:

        if telegram_startup_test():

            logger.info(
                "Startup test message sent ✅"
            )

        else:

            logger.warning(
                "Startup test could not be sent."
            )

    # -----------------------------------------------------
    # Continuous loop
    # -----------------------------------------------------

    while True:

        try:

            logger.info(
                "Searching for new Liverpool news..."
            )

            candidates = get_google_news()

            logger.info(
                "Candidates found: %s",
                len(candidates)
            )

            # Newest first
            candidates.sort(
                key=lambda x: (
                    x.get("published")
                    or time.gmtime(0)
                ),
                reverse=True
            )

            posted = False

            for entry in candidates:

                try:

                    if process_news(
                        entry
                    ):

                        posted = True
                        break

                except Exception as e:

                    logger.exception(
                        "Candidate processing error: %s",
                        e
                    )

            if not posted:

                logger.info(
                    "No suitable new news "
                    "to post right now."
                )

        except Exception as e:

            logger.exception(
                "MAIN LOOP ERROR: %s",
                e
            )

        logger.info(
            "Sleeping for 5 minutes..."
        )

        time.sleep(
            CHECK_EVERY
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    try:

        run_bot()

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped manually."
        )

    except Exception as e:

        logger.exception(
            "FATAL ERROR: %s",
            e
        )

        raise
