"""python manage.py seed_home_shortcuts — fill the home-page catalogue with a ready-made set of links.

Every link is free, works without signing in and is useful on a phone (each was opened as a signed-out
phone, 2026-10-09; chatbots were asked a question and answered). Nothing that needs an account to use
(shopping, banking, paid streaming, mail), no download pages for computer software, no unofficial
streaming sites, nothing paid-to-play or adult. Keep to that when adding more. Picked for phones in
India, Tamil Nadu first.

Adds only what's missing: a category already there (same name, any case) keeps its name, order and
on/off; a link already in its category is left as it is. Safe to run again — but a link the admin
deleted comes back, so after editing the catalogue use --dry-run first to see what it would add.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Max

from mobileapi.models import HomeShortcut, ShortcutCategory

CATALOGUE = [
    # The app shows these before it has fetched the catalogue (ShortcutCatalog.DEFAULTS) — keep in step.
    ("Popular", [
        ("Google", "https://www.google.com"),
        ("YouTube", "https://m.youtube.com"),
        ("Wikipedia", "https://www.wikipedia.org"),
        ("Google Maps", "https://www.google.com/maps"),
        ("Google News", "https://news.google.com"),
        ("Translate", "https://translate.google.com"),
        ("Cricbuzz", "https://www.cricbuzz.com"),
        ("ChatGPT", "https://chatgpt.com"),
    ]),
    # Chatbots that answer without an account, then a few free AI tools.
    ("AI", [
        ("ChatGPT", "https://chatgpt.com"),
        ("Gemini", "https://gemini.google.com"),
        ("Google AI Mode", "https://www.google.com/search?udm=50"),
        ("Perplexity", "https://www.perplexity.ai"),
        ("Duck.ai", "https://duck.ai"),
        ("Brave Ask", "https://search.brave.com/ask"),
        ("AI Image Maker", "https://raphael.app"),
        ("QuillBot", "https://quillbot.com"),
        ("LanguageTool", "https://languagetool.org"),
        ("TTSMaker", "https://ttsmaker.com"),
    ]),
    ("Tamil", [
        ("Sun TV", "https://m.youtube.com/@SunTV"),
        ("Vijay Television", "https://m.youtube.com/@VijayTelevision"),
        ("Zee Tamil", "https://m.youtube.com/@ZeeTamil"),
        ("Saregama Tamil", "https://m.youtube.com/@SaregamaTamil"),
        ("Tamil Songs", "https://www.jiosaavn.com/new-releases/tamil"),
        ("Behindwoods", "https://www.behindwoods.com"),
        ("Cinema Express", "https://www.cinemaexpress.com"),
        ("IndiaGlitz Tamil", "https://www.indiaglitz.com/tamil"),
        ("Wikipedia Tamil", "https://ta.wikipedia.org"),
        ("Project Madurai", "https://www.projectmadurai.org"),
    ]),
    ("Entertainment", [
        ("YouTube Music", "https://music.youtube.com"),
        ("JioSaavn", "https://www.jiosaavn.com"),
        ("MX Player", "https://www.mxplayer.in"),
        ("Radio Garden", "https://radio.garden"),
        ("All India Radio", "https://newsonair.gov.in"),
        ("IMDb", "https://www.imdb.com"),
    ]),
    # Official channels and publishers only — free and legal.
    ("Anime & Cartoons", [
        ("Muse Asia", "https://m.youtube.com/@MuseAsia"),
        ("Ani-One Asia", "https://m.youtube.com/@AniOneAsia"),
        ("WB Kids", "https://m.youtube.com/@WBKids"),
        ("Mr Bean", "https://m.youtube.com/@MrBean"),
        ("Pokémon", "https://m.youtube.com/@pokemon"),
        ("Peppa Pig", "https://m.youtube.com/@PeppaPigOfficial"),
        ("ChuChu TV", "https://m.youtube.com/@ChuChuTVTamil"),
        ("MANGA Plus", "https://mangaplus.shueisha.co.jp"),
        ("Webtoon", "https://www.webtoons.com/en/"),
    ]),
    ("Reels & Feeds", [
        ("YouTube Shorts", "https://m.youtube.com/shorts"),
        ("Snapchat Spotlight", "https://www.snapchat.com/spotlight"),
        ("ShareChat", "https://sharechat.com"),
        ("Reddit", "https://www.reddit.com"),
    ]),
    ("Games", [
        ("Poki", "https://poki.com"),
        ("CrazyGames", "https://www.crazygames.com"),
        ("Chess", "https://lichess.org"),
        ("Sudoku", "https://sudoku.com"),
        ("2048", "https://play2048.co"),
        ("Wordle", "https://www.nytimes.com/games/wordle/index.html"),
        ("Solitaire", "https://www.solitaired.com"),
        ("Snake", "https://www.google.com/fbx?fbx=snake_arcade"),
        ("skribbl.io", "https://skribbl.io"),
        ("Quick, Draw!", "https://quickdraw.withgoogle.com"),
    ]),
    ("Free tools", [
        ("iLovePDF", "https://www.ilovepdf.com"),
        ("Convertio", "https://convertio.co"),
        ("Squoosh", "https://squoosh.app"),
        ("QR Code Monkey", "https://www.qrcode-monkey.com"),
        ("PairDrop", "https://pairdrop.net"),
        ("Wormhole", "https://wormhole.app"),
        ("Tamil Typing", "https://www.google.com/inputtools/try/"),
        ("Audio Cutter", "https://mp3cut.net"),
        ("Speedtest", "https://www.speedtest.net"),
        ("Calculator.net", "https://www.calculator.net"),
        ("EMI Calculator", "https://emicalculator.net"),
        ("Gold Rate Chennai", "https://www.goodreturns.in/gold-rates/chennai.html"),
        ("Drik Panchang", "https://www.drikpanchang.com"),
        ("Windy", "https://www.windy.com"),
        ("VirusTotal", "https://www.virustotal.com"),
        ("GSMArena", "https://www.gsmarena.com"),
        ("Downdetector", "https://downdetector.in"),
    ]),
    ("News", [
        ("Inshorts", "https://inshorts.com/en/read"),
        ("Dailyhunt", "https://www.dailyhunt.in"),
        ("Dinamalar", "https://www.dinamalar.com"),
        ("Daily Thanthi", "https://www.dailythanthi.com"),
        ("Puthiya Thalaimurai", "https://www.puthiyathalaimurai.com"),
        ("BBC Tamil", "https://www.bbc.com/tamil"),
        ("News18 Tamil", "https://tamil.news18.com"),
        ("Oneindia Tamil", "https://tamil.oneindia.com"),
        ("NDTV", "https://www.ndtv.com"),
        ("Times of India", "https://timesofindia.indiatimes.com"),
        ("India Today", "https://www.indiatoday.in"),
        ("Moneycontrol", "https://www.moneycontrol.com"),
    ]),
    ("Sports", [
        ("ESPNcricinfo", "https://www.espncricinfo.com"),
        ("IPL", "https://www.iplt20.com"),
        ("BCCI", "https://www.bcci.tv"),
        ("FlashScore", "https://www.flashscore.in"),
        ("Pro Kabaddi", "https://www.prokabaddi.com"),
    ]),
    ("Education", [
        ("Khan Academy", "https://www.khanacademy.org"),
        ("Tamil Virtual Academy", "https://www.tamilvu.org"),
        ("NCERT Textbooks", "https://ncert.nic.in/textbook.php"),
        ("NPTEL", "https://nptel.ac.in"),
        ("PhET Simulations", "https://phet.colorado.edu"),
        ("GeoGebra", "https://www.geogebra.org/calculator"),
        ("GeeksforGeeks", "https://www.geeksforgeeks.org"),
        ("W3Schools", "https://www.w3schools.com"),
        ("Anna University", "https://www.annauniv.edu"),
    ]),
    # Exam notices, results and job listings — free to read.
    ("Jobs & Exams", [
        ("TNPSC", "https://www.tnpsc.gov.in"),
        ("TN TRB", "https://www.trb.tn.gov.in"),
        ("UPSC", "https://upsc.gov.in"),
        ("SSC", "https://ssc.gov.in"),
        ("IBPS", "https://www.ibps.in"),
        ("National Career Service", "https://www.ncs.gov.in"),
    ]),
    # Information and look-ups that need no account (status checks, notices, forms to read).
    ("Government", [
        ("TN Govt", "https://www.tn.gov.in"),
        ("TNEB", "https://www.tnebltd.org"),
        ("Voter Search", "https://electoralsearch.eci.gov.in"),
        ("Aadhaar (UIDAI)", "https://uidai.gov.in"),
        ("PM Kisan", "https://pmkisan.gov.in"),
        ("Passport Seva", "https://www.passportindia.gov.in"),
        ("India Post", "https://www.indiapost.gov.in"),
        ("IMD Weather", "https://mausam.imd.gov.in"),
        ("Income Tax", "https://www.incometax.gov.in"),
        ("GST", "https://www.gst.gov.in"),
        ("CM Health Insurance", "https://www.cmchistn.com"),
    ]),
    ("Travel & Maps", [
        ("Train Running Status", "https://enquiry.indianrail.gov.in/mntes/"),
        ("PNR Status", "https://www.indianrail.gov.in/enquiry/PNR/PnrEnquiry.html?locale=en"),
        ("RailYatri", "https://www.railyatri.in"),
        ("Chennai Metro", "https://chennaimetrorail.org"),
        ("Fuel Prices", "https://www.mypetrolprice.com"),
    ]),
    ("Recipes", [
        ("Hebbar's Kitchen", "https://hebbarskitchen.com"),
        ("Swasthi's Recipes", "https://www.indianhealthyrecipes.com"),
        ("Padhu's Kitchen", "https://www.padhuskitchen.com"),
        ("Archana's Kitchen", "https://www.archanaskitchen.com"),
    ]),
]


def _same(url):
    return url.strip().rstrip("/").lower()


class Command(BaseCommand):
    help = "Add the ready-made home-shortcut categories and links that aren't there yet (never edits existing ones)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only show what would be added.")

    def handle(self, *args, **options):
        dry = options["dry_run"]
        new_cats, added = [], 0
        with transaction.atomic():
            cat_pos = ShortcutCategory.objects.aggregate(m=Max("position"))["m"] or 0
            by_name = {c.name.lower(): c for c in ShortcutCategory.objects.all()}
            for name, links in CATALOGUE:
                cat = by_name.get(name.lower())
                have, pos = set(), 0
                if cat is None:
                    cat_pos += 1
                    new_cats.append(name)
                    if not dry:
                        cat = ShortcutCategory.objects.create(name=name, position=cat_pos)
                else:
                    have = {_same(u) for u in cat.shortcuts.values_list("url", flat=True)}
                    pos = cat.shortcuts.aggregate(m=Max("position"))["m"] or 0
                missing = [(t, u) for t, u in links if _same(u) not in have]
                for title, url in missing:
                    pos += 1
                    if not dry:
                        HomeShortcut.objects.create(category=cat, title=title, url=url, position=pos)
                added += len(missing)
                if missing:
                    self.stdout.write(f"  {name}: {len(missing)} to add" if dry else f"  {name}: +{len(missing)}")
        total = sum(len(links) for _, links in CATALOGUE)
        if not added:
            self.stdout.write(f"All {total} ready-made shortcuts are already there — nothing changed.")
            return
        what = f"{added} shortcut{'s' if added != 1 else ''}"
        if new_cats:
            what += f" and {len(new_cats)} new categor{'ies' if len(new_cats) != 1 else 'y'} ({', '.join(new_cats)})"
        self.stdout.write(f"Would add {what}. Nothing changed (dry run)." if dry else self.style.SUCCESS(f"Added {what}."))
