"""Render the latestshift narrated demo: real command output + edge-tts narration.

Usage: python demo/render.py   (needs ffmpeg, edge-tts, Pillow, network)
"""
import asyncio
import os
import re
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(HERE, "build")
OUT = os.path.join(ROOT, "demo.mp4")
SDKBIN = "/tmp/lsvenv/bin"
SANDBOX = "/tmp/lsdemo"
W, H, FPS = 1280, 720, 24
VOICE = "en-US-AndrewNeural"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SANS_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BG, FG = (24, 26, 33), (220, 223, 228)
ANSI = {"31": (255, 95, 95), "32": (95, 215, 120), "33": (240, 200, 80),
        "2": (130, 135, 145), "36": (90, 200, 230), "35": (200, 140, 230), "1": FG}
PROMPT = "dev@laptop ~/code $ "

SCENES = [
    {
        "kind": "title",
        "title": "latestshift",
        "sub": "ubuntu-latest is moving to Ubuntu 26.04.\nFind what breaks before your CI does.",
        "say": "This is latest shift, a small open source tool for one very specific, very current problem: "
               "GitHub is moving the ubuntu latest runner label to Ubuntu twenty six oh four.",
    },
    {
        "kind": "bullets",
        "title": "The problem",
        "bullets": [
            "ubuntu-latest -> Ubuntu 26.04: Oct 19 to Nov 19, 2026",
            "   runner-images #14748: 260+ reactions, 130+ comments",
            "Same job, same commit: 24.04 one run, 26.04 the next",
            "No Python 3.9, JDK 25 default, sudo-rs, no conda, apt renames",
            "\"Nothing changed\" and CI is red. A green re-run proves nothing.",
        ],
        "say": "The rollout started on October nineteenth and finishes on November nineteenth. Until then, the same "
               "job can land on twenty four oh four in one run and twenty six oh four in the next. And twenty six oh "
               "four is different: no Python three point nine, Java twenty five by default, a new sudo, no conda, "
               "renamed apt packages, and more. So CI goes red with nothing changed, and a re-run that passes "
               "proves nothing.",
    },
    {
        "kind": "term",
        "cmds": ["latestshift scan shop"],
        "say": "latest shift scan reads your workflow files offline. This is an example repo with made up jobs. "
               "It expands the matrix, so Python three point nine on ubuntu latest is flagged with the exact line, "
               "and it reads package dot json and the Gradle wrapper too: old Playwright can't install browsers on "
               "twenty six oh four, and Gradle eight can't run on Java twenty five. Every finding has a fix and "
               "a source.",
    },
    {
        "kind": "term",
        "cmds": ["latestshift scan httpx; echo \"exit code: $?\""],
        "say": "Here it is on the real workflow files of a popular Python library. Both jobs still test or "
               "publish with Python three point nine on ubuntu latest, which has no build on twenty six oh four. "
               "The exit code is one, so you can gate CI on it.",
    },
    {
        "kind": "term",
        "cmds": ["latestshift runs acme/shop"],
        "env": {"LATESTSHIFT_CASSETTE": "CASSETTE"},
        "say": "latest shift runs answers the other question: is this red job my code, or the new image? It reads "
               "the first part of each job log, finds which image the job got, and tallies passes and failures "
               "per image. This one is a recorded example with made up names. The build job fails only on twenty six "
               "oh four, and in one run a re-run landed on twenty four oh four and passed.",
    },
    {
        "kind": "term",
        "cmds": ["latestshift runs hamarshehmhmd/listwarden --limit 2"],
        "say": "And this is live, against one of my own repositories, reading real job logs from GitHub. Its "
               "ubuntu latest jobs already landed on twenty six oh four, and they pass.",
    },
    {
        "kind": "term",
        "cmds": ["latestshift pin shop", "latestshift pin shop --write | tail -1",
                 "latestshift scan shop; echo \"exit code: $?\""],
        "say": "If you need CI green today, latest shift pin rewrites ubuntu latest to ubuntu twenty four oh four. "
               "It's a dry run until you add write. After that, no job is moving, and the scan exits zero.",
    },
    {
        "kind": "bullets",
        "title": "In CI, and the limits",
        "bullets": [
            "uses: hamarshehmhmd/latestshift@v0.1.0   (annotations + job summary)",
            "CI verifies core rules on the real ubuntu-26.04 image",
            "A clean scan is not a guarantee: try runs-on: ubuntu-26.04",
            "Doesn't follow reusable workflows or fromJSON matrices",
            "Rules are a Nov 9, 2026 snapshot; the image keeps changing",
        ],
        "say": "It also runs as a GitHub Action, with annotations on the exact lines. Its own CI checks the core "
               "rules against the real twenty six oh four image. The limits: a clean scan is not a guarantee, it "
               "doesn't follow reusable workflows, and the rules are a snapshot from November ninth.",
    },
    {
        "kind": "bullets",
        "title": "Try it",
        "bullets": [
            "curl -fsSL https://raw.githubusercontent.com/",
            "   hamarshehmhmd/latestshift/main/install.sh | sh",
            "latestshift scan   ·   latestshift runs   ·   latestshift pin",
            "github.com/hamarshehmhmd/latestshift",
            "GNU AGPL-3.0-or-later (commercial licenses available)",
        ],
        "say": "Install it with the one line installer; it needs Python and PyYAML. It's licensed under the GNU "
               "Affero General Public License version 3 or later, with commercial licenses available from the "
               "author. It's on GitHub at hamarsheh m h m d slash latest shift.",
    },
]


async def tts(text, path):
    import edge_tts
    for attempt in range(8):
        try:
            await edge_tts.Communicate(text, VOICE, rate="+4%").save(path)
            return
        except Exception:
            if os.path.exists(path):
                os.remove(path)
            if attempt == 7:
                raise
            await asyncio.sleep(5)


def duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        text=True,
    )
    return float(out.strip())


EXPORTS = {}


def run_cmd(cmd, cwd, extra=None):
    if cmd.startswith("export ") and "=" in cmd:
        k, v = cmd[7:].split("=", 1)
        EXPORTS[k] = v
        return ""
    env = dict(os.environ, FORCE_COLOR="1", **EXPORTS)
    for k, v in (extra or {}).items():
        env[k] = v.replace("CASSETTE", os.path.join(ROOT, "tests", "fixtures", "cassette.json"))
    env.pop("NO_COLOR", None)
    env["PATH"] = SDKBIN + os.pathsep + env["PATH"]
    env["COLUMNS"] = "120"
    p = subprocess.run(
        ["bash", "-c", "shopt -s nullglob; " + cmd],
        cwd=cwd, capture_output=True, text=True, env=env,
    )
    return (p.stdout + p.stderr).rstrip("\n")


def parse_ansi(line):
    spans, color, bold = [], FG, False
    for part in re.split(r"(\x1b\[[0-9;]*m)", line):
        m = re.match(r"\x1b\[([0-9;]*)m", part)
        if m:
            for code in (m.group(1) or "0").split(";"):
                if code == "0":
                    color, bold = FG, False
                elif code == "1":
                    bold = True
                elif code in ANSI:
                    color = ANSI[code]
        elif part:
            spans.append((part, color, bold))
    return spans


class Term:
    def __init__(self):
        self.font = ImageFont.truetype(MONO, 14)
        self.bold = ImageFont.truetype(MONO_B, 14)
        self.cw = self.font.getlength("M")
        self.lh = 20
        self.cols = int((W - 80) / self.cw)

    def wrap(self, spans):
        lines, cur, n = [], [], 0
        for text, c, b in spans:
            while text:
                room = self.cols - n
                if len(text) <= room:
                    cur.append((text, c, b)); n += len(text); text = ""
                else:
                    cut = text.rfind(" ", 0, room + 1)
                    cut = cut if cut > 0 else room
                    cur.append((text[:cut], c, b)); lines.append(cur); cur, n = [], 0
                    text = "    " + text[cut:].lstrip(" ")
        lines.append(cur)
        return lines

    def frame(self, lines, cursor=False):
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([20, 14, W - 20, H - 14], 12, fill=(30, 33, 41), outline=(60, 64, 76))
        for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            d.ellipse([40 + i * 24, 30, 54 + i * 24, 44], fill=c)
        d.text((W // 2, 37), "~/code — bash", font=ImageFont.truetype(SANS, 15), fill=(150, 155, 165), anchor="mm")
        visible = lines[-30:]
        y = 64
        for ln in visible:
            x = 40
            for text, c, b in ln:
                d.text((x, y), text, font=self.bold if b else self.font, fill=c)
                x += self.cw * len(text)
            y += self.lh
        if cursor:
            last = visible[-1] if visible else []
            x = 40 + self.cw * sum(len(t) for t, _, _ in last)
            d.rectangle([x, y - self.lh + 3, x + self.cw - 2, y - 4], fill=FG)
        return img


def card(scene):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    if scene["kind"] == "title":
        d.text((W // 2, 280), scene["title"], font=ImageFont.truetype(MONO_B, 78), fill=(95, 215, 120), anchor="mm")
        d.multiline_text((W // 2, 420), scene["sub"], font=ImageFont.truetype(SANS, 32), fill=FG, anchor="mm",
                         align="center", spacing=12)
        d.text((W // 2, 620), "github.com/hamarshehmhmd/latestshift  ·  scan · runs · pin · GitHub Action  ·  GNU AGPL-3.0",
               font=ImageFont.truetype(SANS, 20), fill=(150, 155, 165), anchor="mm")
    else:
        d.text((90, 90), scene["title"], font=ImageFont.truetype(SANS_B, 52), fill=(95, 215, 120))
        f = ImageFont.truetype(SANS, 29)
        y = 200
        for b in scene["bullets"]:
            if b and not b.startswith("   "):
                d.text((90, y), "•", font=f, fill=(240, 200, 80))
            d.text((130, y), b.strip(), font=f, fill=FG)
            y += 58 if b.startswith("   ") or not b else 62
    return img


class Writer:
    def __init__(self, path, audio):
        self.p = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", audio,
             "-af", "apad", "-c:v", "libx264", "-preset", "medium", "-crf", "23",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
             "-shortest", path],
            stdin=subprocess.PIPE,
        )
        self.n = 0

    def put(self, img, count=1):
        data = img.tobytes()
        for _ in range(count):
            self.p.stdin.write(data)
        self.n += count

    def close(self):
        self.p.stdin.close()
        self.p.wait()
        if self.p.returncode:
            sys.exit("ffmpeg failed")


def _cap_output(out):
    lines = out.splitlines()
    if len(lines) > 26:
        lines = lines[:24] + ["…", lines[-1]]
    return "\n".join(lines)


def render_scene(i, scene, workdir):
    audio = os.path.join(BUILD, f"s{i}.mp3")
    if not os.path.exists(audio):
        asyncio.run(tts(scene["say"], audio))
    total = int((duration(audio) + 0.7) * FPS)
    path = os.path.join(BUILD, f"s{i}.mp4")
    w = Writer(path, audio)
    if scene["kind"] != "term":
        w.put(card(scene), total)
        w.close()
        return path
    t = Term()
    shown = scene.get("show", scene["cmds"])
    outputs = [_cap_output(run_cmd(c.replace("latestshift ", "latestshift --color always ", 1), workdir, scene.get("env")))
               for c in scene["cmds"]]
    # If show has more lines than cmds (a command displayed as several visual lines
    # that map to one executed command), join show into one typed command per output.
    if len(shown) != len(outputs):
        if len(outputs) == 1:
            shown = [" ".join(s.rstrip("\\").strip() for s in shown)]
        elif len(shown) > len(outputs):
            # first N-1 outputs map 1:1; remaining show lines are the last command
            shown = list(shown[: len(outputs) - 1]) + [
                " ".join(s.rstrip("\\").strip() for s in shown[len(outputs) - 1 :])
            ]
    steps = list(zip(shown, outputs))
    typing = sum(len(s) for s, _ in steps) / 32 * FPS
    pause = max(6, int((total - typing) / max(1, len(steps))) - 4)
    lines = []
    for cmd, out in steps:
        prompt = [(PROMPT, (95, 215, 120), True)]
        for k in range(0, len(cmd) + 1, 2):
            frame = t.frame(lines + t.wrap(prompt + [(cmd[:k], FG, False)]), cursor=True)
            w.put(frame)
        lines += t.wrap(prompt + [(cmd, FG, False)])
        for ol in out.splitlines():
            lines += t.wrap(parse_ansi(ol))
        lines.append([])
        hold = min(pause, max(0, total - w.n))
        w.put(t.frame(lines), hold)
    final = t.frame(lines + t.wrap([(PROMPT, (95, 215, 120), True)]), cursor=True)
    if w.n < total:
        w.put(final, total - w.n)
    w.close()
    return path


def main():
    os.makedirs(BUILD, exist_ok=True)
    if os.path.exists(SANDBOX):
        shutil.rmtree(SANDBOX)
    os.makedirs(SANDBOX)
    shutil.copytree(os.path.join(ROOT, "examples", "webapp"), os.path.join(SANDBOX, "shop"))
    os.makedirs(os.path.join(SANDBOX, "httpx", ".github", "workflows"))
    for f in ("publish.yml", "test-suite.yml"):
        subprocess.run(f"gh api repos/encode/httpx/contents/.github/workflows/{f} --jq .content | base64 -d "
                       f"> httpx/.github/workflows/{f}", shell=True, cwd=SANDBOX, check=True)
    parts = [render_scene(i, s, SANDBOX) for i, s in enumerate(SCENES)]
    with open(os.path.join(BUILD, "list.txt"), "w") as fh:
        fh.writelines(f"file '{p}'\n" for p in parts)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i",
         os.path.join(BUILD, "list.txt"), "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", OUT],
        check=True,
    )
    with open(os.path.join(BUILD, "narration.txt"), "w") as fh:
        fh.write("\n\n".join(s["say"] for s in SCENES) + "\n")
    print(OUT, round(duration(OUT), 1), "s")


if __name__ == "__main__":
    main()
