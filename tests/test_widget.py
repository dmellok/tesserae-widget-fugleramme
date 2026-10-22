"""fugleramme: the manifest, the pin, planning a page from the demo garden and a
BirdNET-Go, the job keys, and the page route drawing a PNG at the asked size.
No network: every fetch of a plate or a face is patched."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
TESSERAE = Path(__file__).resolve().parents[2] / "tesserae"


def _load():
    name = "_tesserae_plugins.fugleramme.server"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "server.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


server = _load()
assets = server.assets
plan = server.plan


def _plate() -> bytes:
    """A bird-shaped blot on a transparent cut-out, as WebP like the real plates."""
    im = Image.new("RGBA", (240, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw.ellipse((30, 40, 210, 170), fill=(232, 224, 205, 255))
    draw.ellipse((60, 60, 180, 150), fill=(120, 80, 40, 255))
    out = io.BytesIO()
    im.save(out, "WEBP", lossless=True)
    return out.getvalue()


PLATE = _plate()


# Japanese names for the demo garden, as BirdNET's ja label list has them.
JA = {
    "Turdus merula": "クロウタドリ",
    "Parus major": "シジュウカラ",
    "Cyanistes caeruleus": "アオガラ",
    "Passer domesticus": "イエスズメ",
    "Fringilla coelebs": "ズアオアトリ",
    "Erithacus rubecula": "ヨーロッパコマドリ",
}
LABEL_LISTS = {"ja": JA, "no": {"Turdus merula": "Svarttrost", "Parus major": "Kjøttmeis"}}

# A face this machine has for each fetched one: Latin for the label italic, a
# CJK-covering one for Noto Sans CJK. None found means the fetch fails.
LATIN_FACES = (
    "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
)
CJK_FACES = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
)


def _have(paths) -> Path | None:
    return next((Path(p) for p in paths if Path(p).is_file()), None)


def _fake_download(fail: set[str] | None = None, faces: bool = False):
    """Writes a synthetic plate where the real one would land, and a label list
    of a few names. Faces fail unless `faces`, so the names are set in a face
    the machine has, as when a fetch fails; with `faces`, a face of the machine's
    own stands in for each one fetched."""
    calls: list[str] = []

    def download(url: str, target: Path) -> bool:
        calls.append(url)
        body: bytes | None = None
        if fail and target.name in fail:
            body = None
        elif target.suffix == ".webp":
            body = PLATE
        elif target.suffix == ".txt":
            names = LABEL_LISTS.get(target.stem, {})
            body = "".join(f"{s}_{c}\n" for s, c in names.items()).encode() or None
        elif faces and target.suffix in (".ttf", ".otf"):
            stand_in = _have(CJK_FACES if "CJK" in target.name else LATIN_FACES)
            body = stand_in.read_bytes() if stand_in else None
        if body is None:
            assets._missed(target)
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return True

    return download, calls


@pytest.fixture
def offline():
    download, calls = _fake_download()
    with patch.object(assets, "_download", download):
        yield calls


def _ctx(tmp_path: Path, w: int = 400, h: int = 300) -> dict:
    return {"data_dir": str(tmp_path), "cell_w": w, "cell_h": h, "preview": False}


SMALL = {"species_limit": "6", "lookback_hours": "24"}


# -- manifest ------------------------------------------------------------------


def test_manifest_is_valid():
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "Fugleramme"
    assert manifest["version"] == "0.1.0"
    assert manifest["tesserae_compat"] == "1.x"
    assert len(manifest["description"]) <= 280
    assert manifest["requires"] == ["network:*"]
    names = [o["name"] for o in manifest["cell_options"]]
    assert len(names) == len(set(names))
    for option in manifest["cell_options"]:
        for key in option.get("show_if", {}):
            assert key in names, (option["name"], key)
        if option["type"] == "select":
            values = [c["value"] for c in option["choices"]]
            assert option["default"] in values, option["name"]
    text = json.dumps(manifest, ensure_ascii=False)
    assert chr(0x2014) not in text  # no em dashes in anything a user reads
    schema = TESSERAE / "schema" / "plugin.schema.json"
    if not schema.exists():
        pytest.skip("no Tesserae checkout beside this repo")
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(manifest, json.loads(schema.read_text(encoding="utf-8")))


def test_options_match_what_the_planner_reads():
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    by_name = {o["name"]: o for o in manifest["cell_options"]}
    defaults = {name: o.get("default") for name, o in by_name.items()}
    opts = plan.options_of(defaults)
    assert opts == plan.Options()  # the manifest's defaults are Fugleramme's
    assert {c["value"] for c in by_name["mode"]["choices"]} == set(plan.MODES)
    assert {c["value"] for c in by_name["font"]["choices"]} == set(plan.fonts.FONTS)
    assert {c["value"] for c in by_name["layout"]["choices"]} == set(plan.packing.LAYOUTS)
    assert {float(c["value"]) for c in by_name["lookback_hours"]["choices"]} == set(plan.LOOKBACKS)


def test_client_imports_nothing_and_keeps_css_comments_plain():
    client = (ROOT / "client.js").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s", client, re.MULTILINE)
    assert "``" not in client
    assert chr(0x2014) not in client


# -- the pin -------------------------------------------------------------------


def test_pin_and_urls():
    assert re.fullmatch(r"[0-9a-f]{40}", assets.PIN)
    assert assets.url_for("artwork/classic/birds/turdus-merula.webp") == (
        "https://raw.githubusercontent.com/arnegiacomo/fugleramme/"
        f"{assets.PIN}/assets/artwork/classic/birds/turdus-merula.webp"
    )
    assert assets.url_for("fonts/bitter/Bitter-Italic.ttf", pin="abc").endswith(
        "/fugleramme/abc/assets/fonts/bitter/Bitter-Italic.ttf"
    )


def test_bundled_records_name_every_plate(tmp_path):
    store = assets.Store(tmp_path)
    manifest = json.loads((store.style_dir / "manifest.json").read_text(encoding="utf-8"))
    geometry = json.loads((store.style_dir / "geometry.json").read_text(encoding="utf-8"))
    birds = [k for k in manifest if k.startswith("birds/")]
    assert len(birds) > 1000
    assert set(birds) <= set(geometry)
    assert all(v.get("source") for v in manifest.values())


def test_store_fetches_once_and_remembers_a_miss(tmp_path):
    download, calls = _fake_download(fail={"pica-pica.webp"})
    store = assets.Store(tmp_path)
    with patch.object(assets, "_download", download):
        missing = store.ensure(
            ["artwork/classic/birds/turdus-merula.webp", "artwork/classic/birds/pica-pica.webp"],
            wait=5,
        )
        assert missing == ["artwork/classic/birds/pica-pica.webp"]
        store.ensure(
            ["artwork/classic/birds/turdus-merula.webp", "artwork/classic/birds/pica-pica.webp"],
            wait=5,
        )
    assert len(calls) == 2  # neither the kept plate nor the remembered miss is asked again


# -- planning ------------------------------------------------------------------


def test_fetch_plans_a_demo_page(tmp_path, offline):
    data = server.fetch(SMALL, {}, ctx=_ctx(tmp_path))
    assert "error" not in data
    assert plan.valid_key(data["job"])
    assert data["demo"] is True
    assert data["mode"] == "collage"
    assert data["birds"] == 6
    assert "Fugleramme" in data["credit"] and "CC BY-SA 4.0" in data["credit"]
    assert data["cell_w"] == 400 and data["cell_h"] == 300 and data["strip"] > 0
    assert (tmp_path / "jobs" / f"{data['job']}.json").exists()
    assert any("raw.githubusercontent.com/arnegiacomo/fugleramme/" in url for url in offline)


@pytest.mark.parametrize("mode", ["latest", "arrival"])
def test_single_plate_pages_plan_one_bird(tmp_path, offline, mode):
    data = server.fetch({"mode": mode}, {}, ctx=_ctx(tmp_path))
    assert data["mode"] == mode
    assert data["birds"] == 1


def test_arrival_is_the_demo_newcomer(tmp_path, offline):
    data = server.fetch({"mode": "arrival"}, {}, ctx=_ctx(tmp_path))
    job = plan.load(data["job"], assets.Store(tmp_path))
    assert list(job["art"]) == ["Accipiter nisus"]


def test_job_key_follows_the_inputs(tmp_path, offline):
    key = lambda options: server.fetch(options, {}, ctx=_ctx(tmp_path))["job"]
    first = key(SMALL)
    assert key(SMALL) == first  # the same page is the same job
    assert key({**SMALL, "paper": "textured"}) != first
    assert key({**SMALL, "layout": "voids"}) != first
    assert key({**SMALL, "names": "en"}) != first
    # The cell's size is not part of the job: the route draws it per size.
    assert server.fetch(SMALL, {}, ctx=_ctx(tmp_path, 800, 480))["job"] == first
    # Each bird's plate is picked again overnight, so the page redraws then.
    with patch.object(plan, "day_ordinal", lambda: 10**6):
        assert key(SMALL) != first


def test_record_and_replay_agree():
    from importlib import import_module

    demo = import_module(f"{server._PACKAGE}.glue.demo").DemoSource()
    recorder = plan.Recorder(demo)
    asked = (
        recorder.species_since(24),
        recorder.last_heard(6),
        recorder.life_list(),
        recorder.recent(500),
    )
    replay = plan.Replay(json.loads(json.dumps(recorder.calls)))
    assert (
        replay.species_since(24),
        replay.last_heard(6),
        replay.life_list(),
        replay.recent(500),
    ) == asked
    dropped = plan.Replay(recorder.calls, frozenset({"Turdus merula"}))
    assert "Turdus merula" not in dict(dropped.species_since(24))


def test_bad_station_is_said_plainly(tmp_path, offline):
    data = server.fetch(
        {"source": "birdweather", "station": "not a station"}, {}, ctx=_ctx(tmp_path)
    )
    assert "BirdWeather station" in data["error"]


# -- a BirdNET-Go ----------------------------------------------------------------


def _birdnet(status: int = 200):
    now = datetime.now(UTC)
    summary = [
        {
            "scientific_name": "Turdus merula",
            "common_name": "Svarttrost",
            "count": 12,
            "first_heard": (now - timedelta(days=30)).isoformat(),
            "last_heard": (now - timedelta(minutes=5)).isoformat(),
        },
        {
            "scientific_name": "Parus major",
            "common_name": "Kjøttmeis",
            "count": 4,
            "first_heard": (now - timedelta(hours=2)).isoformat(),
            "last_heard": (now - timedelta(minutes=20)).isoformat(),
        },
    ]
    recent = [
        {
            "id": 2,
            "timestamp": (now - timedelta(minutes=5)).isoformat(),
            "scientificName": "Turdus merula",
            "commonName": "Svarttrost",
            "confidence": 0.9,
        },
        {
            "id": 1,
            "timestamp": (now - timedelta(minutes=20)).isoformat(),
            "scientificName": "Parus major",
            "commonName": "Kjøttmeis",
            "confidence": 0.8,
        },
    ]

    def answer(self, url, method="GET", headers=None, data=None):
        if status != 200:
            return status, {}, b"{}"
        if "/analytics/species/summary" in url:
            return 200, {}, json.dumps(summary).encode()
        if "/detections/recent" in url:
            return 200, {}, json.dumps(recent).encode()
        if "/species/all" in url:
            rows = [
                {"scientificName": r["scientific_name"], "commonName": r["common_name"]}
                for r in summary
            ]
            return 200, {}, json.dumps({"species": rows}).encode()
        if "/settings/birdnet" in url:
            return 200, {}, json.dumps({"locale": "no"}).encode()
        return 404, {}, b"{}"

    return answer


def test_birdnet_go_page_uses_its_names(tmp_path, offline):
    with patch.object(plan.ApiSource, "_open", _birdnet()):
        data = server.fetch(
            {"address": "birdnet.test:8080", "names": "common"}, {}, ctx=_ctx(tmp_path)
        )
    assert data["demo"] is False and data["birds"] == 2
    job = plan.load(data["job"], assets.Store(tmp_path))
    # The detector's own language is Fugleramme's STATION: /species/all, and
    # the dates follow birdnet.locale.
    assert job["primary"] == "station" and job["locale"] == "no"
    assert job["table"]["station"]["Turdus merula"] == "Svarttrost"


def test_birdnet_go_wanting_a_password_is_said_plainly(tmp_path, offline):
    with patch.object(plan.ApiSource, "_open", _birdnet(status=401)):
        data = server.fetch({"address": "locked.test:8080"}, {}, ctx=_ctx(tmp_path))
    assert "password" in data["error"]


def test_unreachable_birdnet_go_is_an_error_not_an_empty_page(tmp_path, offline):
    def refuse(self, url, method="GET", headers=None, data=None):
        raise server._source.Unavailable("unreachable")

    with patch.object(plan.ApiSource, "_open", refuse):
        data = server.fetch({"address": "gone.test:8080"}, {}, ctx=_ctx(tmp_path))
    assert data["error"].startswith("Couldn't reach BirdNET-Go")


# -- the page route --------------------------------------------------------------


@pytest.fixture
def client(tmp_path, offline):
    flask = pytest.importorskip("flask")
    app = flask.Flask(__name__)
    app.register_blueprint(server.blueprint(), url_prefix="/plugins/fugleramme")
    server._data_dir = tmp_path
    with app.test_client() as test_client:
        yield test_client
    server._data_dir = None


@pytest.mark.parametrize(
    ("options", "size"),
    [
        (SMALL, (320, 200)),
        ({**SMALL, "name_key": True}, (200, 320)),
        ({"mode": "latest", "paper": "textured"}, (300, 300)),
    ],
)
def test_route_draws_a_png_at_the_asked_size(tmp_path, client, options, size):
    data = server.fetch(options, {}, ctx=_ctx(tmp_path))
    w, h = size
    response = client.get(f"/plugins/fugleramme/page.png?job={data['job']}&w={w}&h={h}")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    image = Image.open(io.BytesIO(response.data))
    assert image.size == (w, h)
    assert list((tmp_path / "pages").glob(f"{data['job']}_{w}x{h}*.png"))
    # Drawn once: the second ask is the kept file.
    with patch.object(plan, "draw", side_effect=AssertionError("drawn twice")):
        again = client.get(f"/plugins/fugleramme/page.png?job={data['job']}&w={w}&h={h}")
    assert again.data == response.data


def test_route_refuses_what_it_did_not_plan(client):
    assert client.get("/plugins/fugleramme/page.png?job=../../etc&w=10&h=10").status_code == 404
    assert client.get(f"/plugins/fugleramme/page.png?job={'0' * 20}&w=10&h=10").status_code == 404
    assert client.get(f"/plugins/fugleramme/page.png?job={'0' * 20}&w=x&h=10").status_code == 400


def test_a_plate_that_cannot_be_fetched_leaves_its_bird_off(tmp_path):
    download, _calls = _fake_download(fail={"turdus-merula.webp"})
    with patch.object(assets, "_download", download):
        data = server.fetch({"mode": "collage", "names": "en"}, {}, ctx=_ctx(tmp_path))
        job = plan.load(data["job"], assets.Store(tmp_path))
        missing = {n for n, rel in job["art"].items() if rel.endswith("turdus-merula.webp")}
        png = server.page_png(data["job"], 320, 200, assets.Store(tmp_path))
    assert Image.open(io.BytesIO(png)).size == (320, 200)
    if missing:  # the blackbird wore that plate today, and the page went on without it
        kept = [p.name for p in (tmp_path / "pages").glob(f"{data['job']}_320x200*.png")]
        assert kept and all(name != f"{data['job']}_320x200.png" for name in kept)


def test_cmap_reader_knows_a_face():
    cmap = importlib.import_module(f"{server._PACKAGE}.fugleramme.render.cmap")
    fonts = importlib.import_module(f"{server._PACKAGE}.fugleramme.render.fonts")
    face = next((p for p in fonts.SYSTEM_FONTS if Path(p).is_file()), None)
    if face is None:
        pytest.skip("no system serif here")
    points = cmap.codepoints(face)
    assert points and ord("A") in points and ord("z") in points
    assert cmap.codepoints(ROOT / "plugin.json") is None


# -- languages -------------------------------------------------------------------


def test_language_catalog_matches_the_manifest():
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    by_name = {o["name"]: o for o in manifest["cell_options"]}
    primary = [c["value"] for c in by_name["names"]["choices"]]
    assert primary[:4] == ["sci", "common", "en", "nb"]
    assert set(primary) == set(plan.LANGUAGES)
    assert {"ja", "zh", "ko", "he", "ar", "th", "ml", "pt-br"} <= set(primary)
    second = [c["value"] for c in by_name["second"]["choices"]]
    assert second == ["", *primary]
    assert set(assets.LABEL_FILES) == set(plan.LANGUAGES) - {"sci", "common", "en"}
    assert assets.LABELS.format("ja").endswith("BirdNET_GLOBAL_6K_V2.4_Labels_ja.txt")


def test_names_come_from_the_label_list_in_the_demo_garden(tmp_path, offline):
    data = server.fetch({"names": "ja", **SMALL}, {}, ctx=_ctx(tmp_path))
    job = plan.load(data["job"], assets.Store(tmp_path))
    assert job["primary"] == "ja"
    assert job["table"]["ja"]["Turdus merula"] == "クロウタドリ"
    assert any(url.endswith("Labels_ja.txt") for url in offline)


def test_second_language_stacks_under_in_parentheses(tmp_path, offline):
    data = server.fetch({"names": "en", "second": "nb", **SMALL}, {}, ctx=_ctx(tmp_path))
    job = plan.load(data["job"], assets.Store(tmp_path))
    namer = plan.namer_for(job)
    assert namer.label("Turdus merula") == "Eurasian Blackbird\n(Svarttrost)"
    # A bird the second language has no name for is named in it scientifically.
    assert namer.label("Sitta europaea") == "Eurasian Nuthatch\n(Sitta europaea)"
    page = importlib.import_module(f"{server._PACKAGE}.fugleramme.render.page")
    fonts = importlib.import_module(f"{server._PACKAGE}.fugleramme.render.fonts")
    font = fonts.load("gentium", 24)
    one = page.text_mask("Eurasian Blackbird", font, flat=True)
    two = page.text_mask(namer.label("Turdus merula"), font, flat=True)
    assert two.height > one.height * 1.6


def test_a_japanese_name_is_set_in_the_fallback_face(tmp_path):
    if _have(CJK_FACES) is None or _have(LATIN_FACES) is None:
        pytest.skip("no CJK face on this machine to stand in for Noto Sans CJK")
    download, _calls = _fake_download(faces=True)
    with patch.object(assets, "_download", download):
        data = server.fetch({"names": "ja", "name_key": True, **SMALL}, {}, ctx=_ctx(tmp_path))
        store = assets.Store(tmp_path)
        job = plan.load(data["job"], store)
        assert "fonts/noto/NotoSansCJKsc-Regular.otf" in job["fonts"]
        ctx = plan.context_of(job, plan.Replay(job["calls"]), store, (320, 200))
        modes = importlib.import_module(f"{server._PACKAGE}.fugleramme.modes")
        fonts = importlib.import_module(f"{server._PACKAGE}.fugleramme.render.fonts")
        assert modes._labeller(ctx)("Turdus merula") == "クロウタドリ"
        face = fonts.face("クロウタドリ", fonts.load(job["font"], 24))
        assert face.path.endswith("NotoSansCJKsc-Regular.otf")
        png = server.page_png(data["job"], 320, 200, store)
    assert Image.open(io.BytesIO(png)).size == (320, 200)


def test_a_name_no_face_can_set_shows_as_the_scientific_name(tmp_path):
    download, _calls = _fake_download(fail={"NotoSansCJKsc-Regular.otf"}, faces=True)
    with patch.object(assets, "_download", download):
        data = server.fetch({"names": "ja", **SMALL}, {}, ctx=_ctx(tmp_path))
        store = assets.Store(tmp_path)
        job = plan.load(data["job"], store)
        ctx = plan.context_of(job, plan.Replay(job["calls"]), store, (320, 200))
        modes = importlib.import_module(f"{server._PACKAGE}.fugleramme.modes")
        assert job["table"]["ja"]["Turdus merula"] == "クロウタドリ"
        assert modes._labeller(ctx)("Turdus merula") == "Turdus merula"


def test_credit_line_is_always_on(tmp_path, offline):
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert "credit" not in {o["name"] for o in manifest["cell_options"]}
    client = (ROOT / "client.js").read_text(encoding="utf-8")
    assert "options.credit" not in client and "opts.credit" not in client
    data = server.fetch({"mode": "latest"}, {}, ctx=_ctx(tmp_path))
    assert data["strip"] > 0
    assert data["credit_short"] == "Plates from Fugleramme, CC BY-SA 4.0"
