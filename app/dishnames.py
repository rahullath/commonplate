"""Tidy a dish name typed by hand: spacing, capitals, and a "did you mean" for slips like
Briyani or Cheeshy. Spelling is only ever suggested, never changed behind the user's back."""
import difflib
import re

# Words we know even when no dish on the menu uses them yet.
KNOWN = """
biryani pulao pulav korma kofta makhani kadai kadhai jalfrezi tikka masala paneer tandoori butter
dal dhal tadka fry curry gravy sabzi sabji subzi aloo gobi bhindi baingan matar mutter palak methi
jeera rice thali combo roti naan paratha phulka puri poori bhatura chole chana rajma kadhi raita
curd lassi chai coffee tea shake milkshake juice soda water lemonade falooda kulfi ice cream
gulab jamun halwa kheer rasmalai jalebi samosa kachori pakora pakoda bhaji vada pav bhaji pani
chaat bhel sev papdi dahi puri dosa idli sambar chutney uttapam upma poha sandwich burger pizza
pasta noodles hakka manchurian schezwan momos soup sweet corn cheese cheesy chilli garlic
mushroom veg vegetable mixed special lunch dinner box half full large small mini regular extra
disc disk pickle papad salad achar thanda mango rose masala jain tawa kebab seekh hara bhara
""".split()

# Slips we have seen. The key is what was typed, the value is the usual spelling.
ALIASES = {
    "briyani": "biryani", "biriyani": "biryani", "biryaani": "biryani", "briyaani": "biryani",
    "cheeshy": "cheesy", "cheezy": "cheesy", "disk": "disc", "panner": "paneer", "paner": "paneer",
    "tiki": "tikki", "dhaal": "dal", "daal": "dal", "chaay": "chai", "pakoda": "pakora",
}
# These are all fine as typed.
ACCEPT = {"sabzi", "sabji", "subzi", "pakoda", "pakora", "dhal", "dal", "kadai", "kadhai", "pulao", "pulav"}

_WORD = re.compile(r"[A-Za-z]+")
_SMALL = {"and", "of", "with", "in", "on", "the", "a", "n"}


def _title(word: str, first: bool) -> str:
    w = word.lower()
    if not first and w in _SMALL:
        return w
    if "'" in w:  # joe's -> Joe's
        return "'".join(p.capitalize() if i == 0 else p for i, p in enumerate(w.split("'")))
    return w[:1].upper() + w[1:]


def clean(name: str) -> str:
    """One space between words, no stray punctuation, each word capitalised. (Half) stays as is."""
    s = re.sub(r"[\t\r\n]+", " ", name or "")
    s = re.sub(r"[^\w\s'&()./+-]", "", s)
    s = re.sub(r"\s+", " ", s).strip(" -.")
    out = []
    for i, tok in enumerate(s.split(" ")):
        if re.fullmatch(r"\(?[A-Za-z']+\)?", tok):
            lead = "(" if tok.startswith("(") else ""
            trail = ")" if tok.endswith(")") else ""
            out.append(lead + _title(tok.strip("()"), i == 0) + trail)
        else:
            out.append(tok)
    return " ".join(out)


def vocabulary(menu_names: list[str]) -> set[str]:
    words = set(KNOWN)
    for n in menu_names:
        words.update(w.lower() for w in _WORD.findall(n) if len(w) > 2)
    return words


def suggestions(name: str, menu_names: list[str]) -> list[dict]:
    """[{"from": "Briyani", "to": "Biryani"}, ...] for words that look like slips."""
    vocab = vocabulary(menu_names)
    out, seen = [], set()
    for w in _WORD.findall(name):
        lw = w.lower()
        if lw in seen or len(lw) < 3 or lw in ACCEPT:
            continue
        seen.add(lw)
        fix = ALIASES.get(lw)
        if not fix and lw not in vocab:
            # Same first letter and a close match, so ordinary words are not "corrected".
            m = difflib.get_close_matches(lw, [v for v in vocab if v[0] == lw[0] and abs(len(v) - len(lw)) <= 2], n=1, cutoff=0.82)
            fix = m[0] if m else None
        if fix and fix != lw:
            out.append({"from": w, "to": _title(fix, True)})
    return out


def apply(name: str, subs: list[dict]) -> str:
    s = name
    for sub in subs:
        s = re.sub(r"\b" + re.escape(sub["from"]) + r"\b", sub["to"], s, flags=re.I)
    return clean(s)


def key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())
