"""Deterministic red-flag scan, independent of any LLM (MEDGEMMA_DO_SETUP.md §6.4).

Any hit sets a MINIMUM urgency of "urgent" that the LLM cannot lower.
Patterns/thresholds are placeholders to be confirmed by a clinician;
numeric thresholds live in config (settings.redflag_*).

CONTRACT (implemented by the clinical task):

    @dataclass(frozen=True)
    class RedFlagHit:
        category: str     chest_pain | breathing | fainting | stroke | bleeding |
                          suicidal | bp_crisis | hypoglycemia
        matched: str      the matched text / value (for the doctor + rationale)
        source: str       "text" | "vital"

    scan_text(text: str) -> list[RedFlagHit]
        Case-insensitive English + German patterns over the given text.
        Must not fire on clearly negated mentions in the same clause
        ("no chest pain", "keine Brustschmerzen") - keep the negation window
        short so real complaints are never suppressed.
        Also catches spoken/written readings: BP "190/125", "190 over 125",
        "190 zu 125"; glucose "blood sugar 45" / "Zucker 45".

    scan_vitals(captured: CapturedData | dict | None) -> list[RedFlagHit]
        BP systolic > settings.redflag_bp_systolic_above or diastolic >
        settings.redflag_bp_diastolic_above; blood_sugar <
        settings.redflag_glucose_below (mg/dL).

    scan(transcript: str, captured=None) -> list[RedFlagHit]
        scan_text over PATIENT turns only (services.transcripts.patient_text;
        the agent's own probe "any chest pain?" must not trigger) + scan_vitals.
        Deduplicated by (category, matched).

    rule_urgency(hits) -> str      "urgent" if hits else "none"

Extra helpers (also used by the clinical extraction guard):

    normalize_spoken_numbers(text) -> str
        "one forty over ninety" -> "140 over 90", "a hundred and forty" ->
        "140", "seven and a half" -> "7.5", "hundertvierzig zu neunzig" ->
        "140 zu 90".
    bp_readings(text) -> list[tuple[int, int]]
        Every "S/D", "S over D", "S zu D" pair in the (normalized) text.

Design notes - the bias is deliberately towards flagging: a false positive
costs a doctor a minute of review, a false negative can cost a patient.

- Negation only suppresses a mention when a negator (no, not, never,
  don't, without, kein/keine/nicht/nie, ...) sits within NEGATION_WINDOW
  words before it, in the same clause (clauses end at . , ; : ! ? and at
  "but"/"aber"), AND every word in between is neutral filler ("I don't have
  ANY chest pain"). A content word or intensifier in between keeps the
  mention ("I don't know why my chest hurts", "never had SUCH chest pain"),
  and so does a trailing "like this" / "before" / "unless" / "wie jetzt".
- "No chest pain or shortness of breath" negates the whole or-list.
- Suicidal statements are never negated: any mention goes to a human.
- Numeric readings are facts, not mentions: they are never negated.
- Glucose readings below 30 without a unit are read as mmol/L (x18):
  mg/dL values that low are incompatible with a normal phone conversation,
  while 3-30 is the everyday mmol/L range in Germany.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.config import settings
from app.schemas.clinical import CapturedData
from app.services import transcripts

NEGATION_WINDOW = 3  # words before a mention that may hold its negator
MMOL_TO_MG_DL = 18.0
_MMOL_AMBIGUOUS_BELOW = 30.0  # unit-less glucose readings below this are mmol/L


@dataclass(frozen=True)
class RedFlagHit:
    category: str
    matched: str
    source: str  # "text" | "vital"


# --- text preparation -------------------------------------------------------

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "´": "'", "`": "'"})


def _prep(text: str | None) -> str:
    """NFC + straight apostrophes, so "can’t" and "can't" match alike."""
    return unicodedata.normalize("NFC", text or "").translate(_APOSTROPHES)


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


# --- phrase patterns ----------------------------------------------------------
# Each category is one case-insensitive alternation wrapped in \b...\b.
# Umlauts accept the umlaut, the "ae/oe/ue" spelling, and a bare vowel.

_A, _O, _U, _SS = "(?:ä|ae|a)", "(?:ö|oe|o)", "(?:ü|ue|u)", "(?:ß|ss)"
_CANT = r"(?:can'?t|cannot|can\s+not|could\s*n'?t|could\s+not|unable\s+to)"

_CATEGORY_PATTERNS: dict[str, list[str]] = {
    "chest_pain": [
        r"chest\s+(?:pains?|tightness|pressure|discomfort)",
        r"chest\s+(?:hurts|is\s+hurting|aches|is\s+aching|feels?\s+(?:tight|heavy)|is\s+(?:tight|heavy))",
        r"(?:pains?|tightness|pressure|discomfort)\s+(?:in|on|across)\s+(?:my|the)\s+chest",
        r"(?:tight|heavy)\s+chest",
        r"heart\s+attack",
        # German
        r"brust(?:korb)?schmerz\w*",
        r"schmerz\w*\s+(?:in\s+der|an\s+der|im)\s+brust(?:korb)?",
        rf"enge(?:gef{_U}hl)?\s+in\s+der\s+brust",
        r"brustenge",
        r"druck\s+auf\s+der\s+brust",
        rf"brust\s+(?:tut\s+weh|schmerzt|ist\s+eng|dr{_U}ckt)",
        r"herzinfarkt",
    ],
    "breathing": [
        rf"{_CANT}\s+(?:really\s+)?breathe?",
        rf"{_CANT}\s+(?:get|catch)\s+(?:my\s+)?(?:breath|air)",
        r"can\s+(?:barely|hardly)\s+breathe?",
        r"short(?:ness)?\s+of\s+breath",
        r"(?:trouble|difficulty|difficulties|problems?|hard\s+time)\s+(?:with\s+)?(?:my\s+)?breathing",
        r"breathing\s+(?:difficult\w*|problems?|trouble|is\s+(?:hard|difficult))",
        r"(?:struggling|struggle|hard|difficult)\s+to\s+breathe?",
        r"breathless(?:ness)?",
        r"gasping\s+for\s+(?:air|breath)",
        r"out\s+of\s+breath",
        # German
        r"atemnot", r"luftnot", r"kurzatmig\w*", r"atembeschwerden",
        r"kann\s+(?:\w+\s+){0,3}?(?:kaum|nicht|schlecht|schwer)\s+(?:mehr\s+)?atmen",
        r"(?:kriege|krieg|kriegte|bekomme|bekomm|bekam)\s+(?:\w+\s+){0,4}?"
        r"(?:kaum|keine|schlecht|schwer|wenig)\s+luft",
        r"(?:keine|kaum|schlecht|schwer)\s+luft",
        rf"atmen\s+f{_A}llt\s+(?:mir\s+)?(?:sehr\s+)?schwer",
    ],
    "fainting": [
        r"faint(?:ed|ing)",
        r"(?:feel|felt|feeling|went|going)\s+faint",
        r"pass(?:ed|ing)?\s+out",
        r"black(?:ed|ing)?\s+out",
        r"los(?:t|e|ing)\s+consciousness",
        r"unconscious",
        r"collapsed?",
        # Severe dizziness is part of the agent's red-flag probe (presyncope).
        r"(?:severe|severely|extreme|extremely|really\s+bad)\s+dizz\w*",
        # German
        rf"ohnm{_A}chtig\w*", r"ohnmacht\w*", rf"bewu{_SS}tlos\w*",
        r"umgekippt", r"zusammengebrochen", r"kollabiert",
        r"(?:starke[rnms]?|heftige[rnms]?|extreme[rnms]?)\s+schwindel\w*",
        r"extrem\s+schwindel\w*",
    ],
    "stroke": [
        r"stroke",
        r"(?:face|facial|mouth|lip|eyelid)\s+(?:is\s+|was\s+|has\s+been\s+|started\s+)?droop\w*",
        r"droop(?:y|ing)\s+(?:face|mouth|lip|eyelid|side)",
        r"(?:face|mouth)\s+(?:is\s+)?(?:hanging|sagging|crooked)",
        r"slurr(?:ed|ing)\s+(?:my\s+)?(?:speech|words)",
        r"(?:speech|words|talking)\s+(?:is\s+|are\s+|was\s+|were\s+|sounds?\s+)?slurr\w*",
        r"slurring",
        rf"{_CANT}\s+(?:really\s+)?(?:move|feel|lift)\s+my\s+(?:left\s+|right\s+)?(?:arm|leg|hand|foot|face|side)",
        r"(?:one|left|right)\s+side\s+(?:of\s+(?:my|the)\s+(?:body|face)\s+)?"
        r"(?:is\s+|feels\s+|went\s+|has\s+gone\s+|got\s+)?(?:numb|weak|paraly[sz]ed|limp|dead)",
        r"(?:numb(?:ness)?|weak(?:ness)?|paraly[sz]ed|tingling)\s+(?:on|in|down)\s+"
        r"(?:one|the\s+left|the\s+right|my\s+left|my\s+right)\s+side",
        r"paraly[sz]ed", r"paralysis",
        # German
        r"schlaganfall", r"hirnschlag", r"apoplex\w*",
        rf"h{_A}ngende[rnms]?\s+mundwinkel", rf"mundwinkel\s+h{_A}ngt", rf"gesicht\s+h{_A}ngt",
        r"verwaschene[rnms]?\s+sprache", r"lall(?:e|en|t)",
        rf"l{_A}hmung\w*", rf"gel{_A}hmt\w*",
        rf"kann\s+(?:meinen?\s+|den\s+|die\s+)?(?:linken?\s+|rechten?\s+)?(?:arm|bein|hand|fu{_SS})\s+"
        r"nicht\s+(?:mehr\s+)?bewegen",
        rf"(?:eine|einer|linke|rechte)\s+(?:seite|k{_O}rperh{_A}lfte)\s+(?:ist\s+)?(?:taub|schwach|gel{_A}hmt)",
        r"taub\w*\s+(?:auf|an)\s+(?:einer|der\s+linken|der\s+rechten)\s+seite",
    ],
    "bleeding": [
        r"bleeding\s+(?:heavily|a\s+lot|badly|profusely|nonstop|non-stop)",
        r"(?:heavy|severe|serious|massive|uncontrolled|profuse)\s+bleeding",
        r"(?:won'?t|will\s+not|does\s*n'?t|does\s+not|can'?t|cannot)\s+stop\s+bleeding",
        r"bleeding\s+(?:won'?t|will\s+not|does\s*n'?t|does\s+not|is\s+not|isn'?t)\s+stop\w*",
        r"(?:a\s+)?lots?\s+of\s+blood",
        r"blood\s+everywhere",
        r"(?:coughing|cough|vomiting|throwing)\s+(?:up\s+)?blood",
        # German
        r"starke[rnms]?\s+blutung\w*", r"blutet\s+(?:sehr\s+)?stark",
        rf"blutet\s+(?:und\s+)?h{_O}rt\s+nicht\s+(?:mehr\s+)?auf",
        rf"h{_O}rt\s+nicht\s+auf\s+zu\s+bluten",
        r"viel\s+blut", r"blut\s+(?:husten|gehustet|erbrechen|erbrochen)",
    ],
    "suicidal": [
        r"suicid\w*", r"kill\s+myself", r"end\s+(?:my\s+(?:own\s+)?life|it\s+all)",
        r"take\s+my\s+(?:own\s+)?life",
        r"(?:want|wanna|wish|going)\s+(?:to\s+)?(?:die|be\s+dead)",
        r"(?:do\s*n'?t|do\s+not)\s+want\s+to\s+(?:live|be\s+alive)",
        r"better\s+off\s+dead", r"no\s+reason\s+to\s+live",
        r"(?:hurt|harm)\s+myself", r"self[\s-]?harm\w*",
        # German
        r"suizid\w*", r"selbstmord\w*", r"mich\s+(?:selbst\s+)?umbringen",
        rf"mich\s+(?:selbst\s+)?t{_O}ten", r"nicht\s+mehr\s+leben",
        rf"(?:will|m{_O}chte)\s+(?:nur\s+noch\s+)?sterben", rf"lebensm{_U}de",
        r"mir\s+(?:etwas|was)\s+antun", r"(?:meinem\s+)?leben\s+ein\s+ende\s+(?:setzen|machen)",
    ],
    "bp_crisis": [
        r"hypertensive\s+(?:crisis|emergency|urgency)",
        r"blutdruckkrise", r"hypertensive\s+krise", r"hochdruckkrise",
    ],
    "hypoglycemia": [
        r"hypoglyc\w*", r"hypo", r"unterzucker\w*",
        r"(?:very|really|extremely|dangerously)\s+low\s+(?:blood\s+)?sugar",
    ],
}
_NOT_NEGATABLE = {"suicidal"}

_CATEGORY_RES: list[tuple[str, re.Pattern[str], bool]] = [
    (category, re.compile(r"\b(?:" + "|".join(patterns) + r")\b", re.IGNORECASE),
     category not in _NOT_NEGATABLE)
    for category, patterns in _CATEGORY_PATTERNS.items()
]

# --- negation -----------------------------------------------------------------

_NEGATORS = {
    "no", "not", "never", "without", "neither", "nor",
    "don't", "dont", "doesn't", "doesnt", "didn't", "didnt", "haven't", "havent",
    "hasn't", "hasnt", "hadn't", "hadnt", "isn't", "isnt", "wasn't", "wasnt",
    "aren't", "arent", "weren't", "werent", "ain't",
    "kein", "keine", "keinen", "keinem", "keiner", "keines",
    "nicht", "nie", "niemals", "ohne", "weder",
}
# Neutral words that may sit between a negator and the mention. Intensifiers
# ("such", "so", "bad", "severe", "starke") are deliberately NOT here.
_FILLERS = {
    "a", "an", "any", "the", "some", "much", "more", "real", "really", "longer",
    "further", "other", "sign", "signs", "of", "symptom", "symptoms", "i", "i've",
    "ive", "i'm", "im", "have", "has", "had", "having", "feel", "feels", "felt",
    "feeling", "notice", "noticed", "noticing", "experience", "experienced",
    "experiencing", "get", "gets", "got", "getting", "gotten", "see", "seen", "been",
    "ever", "at", "all", "there", "there's", "is", "was", "are", "were", "it", "it's",
    "anymore", "currently", "today", "now", "recently", "lately", "yet", "actually",
    "even", "suffer", "suffering", "suffered", "problem", "problems", "with",
    # German
    "ich", "habe", "hab", "hatte", "hatten", "haben", "hat", "mehr", "wirklich",
    "irgendwelche", "irgendwelchen", "irgendeine", "irgendeinen", "eine", "einen",
    "ein", "gar", "überhaupt", "ueberhaupt", "bemerkt", "gespürt", "gespuert",
    "gemerkt", "gehabt", "verspürt", "verspuert", "heute", "momentan", "gerade",
    "derzeit", "aktuell", "bisschen", "probleme", "beschwerden", "anzeichen", "mit",
}
_LIST_JOINERS = {"or", "nor", "oder"}
_LIST_FILLERS = {"any", "a", "an", "the", "other", "even", "also", "auch",
                 "irgendwelche", "eine", "einen"}

_WORD_RE = re.compile(r"[\w']+")
_CLAUSE_PUNCT = r"[.,;:!?\n]|\s[-–—]+\s"
_CLAUSE_WORDS = r"\b(?:but|however|although|though|except|aber|sondern|jedoch|doch|allerdings|au(?:ß|ss)er)\b"
_CLAUSE_BREAK_RE = re.compile(rf"{_CLAUSE_PUNCT}|{_CLAUSE_WORDS}", re.IGNORECASE)
# The look-ahead stops at sentence ends and contrast words, NOT at commas
# or "except" ("no chest pain, except when I walk" is a complaint).
_SENTENCE_BREAK_RE = re.compile(
    r"[.;!?\n]|\b(?:but|however|although|though|aber|sondern|jedoch|allerdings)\b", re.IGNORECASE)
_AFFIRM_AFTER_RE = re.compile(
    r"\b(?:like\s+(?:this|that|now|today)|this\s+(?:bad|strong|severe)|so\s+(?:bad|strong|severe)"
    r"|before|until\s+(?:now|today)|till\s+(?:now|today)|unless|except|only\s+when|when\s+i"
    r"|whenever|wie\s+(?:jetzt|heute|gerade|diese[nrsm]?)|so\s+(?:stark|schlimm|heftig)"
    r"|bisher|bis\s+jetzt|vorher|zuvor|au(?:ß|ss)er|nur\s+(?:wenn|bei)|wenn\s+ich)\b",
    re.IGNORECASE,
)


def _negated_before(text: str, start: int) -> bool:
    prefix = text[max(0, start - 200):start]
    cut = 0
    for m in _CLAUSE_BREAK_RE.finditer(prefix):
        cut = m.end()
    words = _WORD_RE.findall(prefix[cut:].lower())
    for word in reversed(words[-NEGATION_WINDOW:]):
        if word in _NEGATORS:
            return True
        if word not in _FILLERS:
            return False
    return False


def _affirmed_after(text: str, end: int) -> bool:
    suffix = text[end:end + 200]
    m = _SENTENCE_BREAK_RE.search(suffix)
    if m:
        suffix = suffix[:m.start()]
    window = " ".join(_WORD_RE.findall(suffix)[:8])
    return bool(_AFFIRM_AFTER_RE.search(window))


def _continues_or_list(gap: str) -> bool:
    """True for the gap in "no chest pain OR shortness of breath"."""
    if re.search(r"[.;:!?\n]", gap) or re.search(_CLAUSE_WORDS, gap, re.IGNORECASE):
        return False
    words = _WORD_RE.findall(gap.lower())
    return (any(w in _LIST_JOINERS for w in words)
            and all(w in _LIST_JOINERS or w in _LIST_FILLERS for w in words))


def _scan_mentions(text: str) -> list[RedFlagHit]:
    mentions: list[tuple[int, int, str, bool]] = []
    for category, pattern, negatable in _CATEGORY_RES:
        for m in pattern.finditer(text):
            mentions.append((m.start(), m.end(), category, negatable))
    mentions.sort(key=lambda x: (x[0], -x[1]))

    hits: list[RedFlagHit] = []
    prev_end, prev_negated = None, False
    for start, end, category, negatable in mentions:
        negated = False
        if negatable and not _affirmed_after(text, end):
            negated = _negated_before(text, start) or (
                prev_negated and prev_end is not None and prev_end <= start
                and _continues_or_list(text[prev_end:start]))
        if not negated:
            matched = " ".join(text[start:end].lower().split())
            hits.append(RedFlagHit(category, matched, "text"))
        prev_end, prev_negated = end, negated
    return hits


# --- spoken numbers -----------------------------------------------------------

_EN_UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9}
_EN_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
             "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
            "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_EN_VALUES = {**_EN_UNITS, **_EN_TEENS, **_EN_TENS, "zero": 0, "oh": 0}


def _alt(words) -> str:
    return "(?:" + "|".join(sorted(words, key=len, reverse=True)) + ")"


_EN_U = _alt(_EN_UNITS)
_EN_U_NO_ONE = _alt(w for w in _EN_UNITS if w != "one")  # "one" alone is often a pronoun
_EN_T = _alt(_EN_TEENS)
_EN_D = _alt(_EN_TENS)
_SEP = r"(?:\s+|-)"
_EN_TENS_FULL = rf"(?:{_EN_D}(?:{_SEP}{_EN_U})?|{_EN_T}|{_EN_U}|zero)"
_EN_NUMBER_RE = re.compile(
    rf"\b(?:"
    rf"(?:(?:a|{_EN_U}){_SEP})?hundred(?:{_SEP}(?:and{_SEP})?{_EN_TENS_FULL})?"  # a hundred and forty
    rf"|{_EN_U}{_SEP}(?:{_EN_D}(?:{_SEP}{_EN_U})?|{_EN_T}|oh{_SEP}{_EN_U})"      # one forty, one oh five
    rf"|one(?={_SEP}and{_SEP}a{_SEP}half)"                                       # one and a half
    rf"|{_EN_D}(?:{_SEP}{_EN_U})?|{_EN_T}|{_EN_U_NO_ONE}|zero"
    rf")(?:{_SEP}and{_SEP}a{_SEP}half|{_SEP}point{_SEP}(?:{_EN_U}|zero))?\b",
    re.IGNORECASE,
)


def _en_value(phrase: str) -> float:
    words = [w for w in re.split(r"[\s-]+", phrase.lower()) if w]
    frac = 0.0
    if words[-3:] == ["and", "a", "half"]:
        frac, words = 0.5, words[:-3]
    elif len(words) >= 2 and words[-2] == "point":
        frac, words = _EN_VALUES[words[-1]] / 10, words[:-2]
    if "hundred" in words:
        i = words.index("hundred")
        mult = _EN_VALUES.get(words[i - 1], 1) if i > 0 else 1  # "a hundred" -> 1
        rest = [w for w in words[i + 1:] if w != "and"]
        return mult * 100 + sum(_EN_VALUES[w] for w in rest) + frac
    if len(words) >= 2 and words[0] in _EN_UNITS:  # colloquial "one forty" = 140
        return _EN_UNITS[words[0]] * 100 + sum(_EN_VALUES[w] for w in words[1:]) + frac
    return sum(_EN_VALUES[w] for w in words) + frac


_DE_UNITS = {"ein": 1, "eins": 1, "eine": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5,
             "fuenf": 5, "funf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9}
_DE_TEENS = {"zehn": 10, "elf": 11, "zwölf": 12, "zwoelf": 12, "zwolf": 12, "dreizehn": 13,
             "vierzehn": 14, "fünfzehn": 15, "fuenfzehn": 15, "funfzehn": 15, "sechzehn": 16,
             "siebzehn": 17, "achtzehn": 18, "neunzehn": 19}
_DE_TENS = {"zwanzig": 20, "dreißig": 30, "dreissig": 30, "vierzig": 40, "fünfzig": 50,
            "fuenfzig": 50, "funfzig": 50, "sechzig": 60, "siebzig": 70, "achtzig": 80,
            "neunzig": 90}
_DE_U_COMPOUND = _alt(w for w in _DE_UNITS if w not in ("eins", "eine"))  # ein|zwei|... in compounds
_DE_U_ALONE = _alt(w for w in _DE_UNITS if w not in ("ein", "eine"))      # articles are not numbers
_DE_BELOW_100 = rf"(?:{_DE_U_COMPOUND}und{_alt(_DE_TENS)}|{_alt(_DE_TENS)}|{_alt(_DE_TEENS)}|{_DE_U_ALONE})"
_DE_NUMBER_RE = re.compile(
    rf"\b(?:(?:{_DE_U_COMPOUND})?hundert(?:und)?(?:{_DE_BELOW_100})?|{_DE_BELOW_100})"
    rf"(?:einhalb)?(?:\s+komma\s+(?:{_DE_U_ALONE}|null))?\b",
    re.IGNORECASE,
)


def _de_value(phrase: str) -> float:
    w = phrase.lower()
    frac = 0.0
    m = re.search(r"\s+komma\s+(\w+)$", w)
    if m:
        frac = _DE_UNITS.get(m.group(1), 0) / 10
        w = w[:m.start()]
    if w.endswith("einhalb") and len(w) > len("einhalb"):
        frac += 0.5
        w = w[: -len("einhalb")]
    total = 0
    if "hundert" in w:
        pre, _, w = w.partition("hundert")
        total += (_DE_UNITS[pre] if pre else 1) * 100
        if w.startswith("und"):
            w = w[3:]
    if w:
        if w in _DE_TENS:
            total += _DE_TENS[w]
        elif w in _DE_TEENS:
            total += _DE_TEENS[w]
        elif w in _DE_UNITS:
            total += _DE_UNITS[w]
        else:  # e.g. "fünfundzwanzig"
            unit, _, tens = w.partition("und")
            total += _DE_UNITS[unit] + _DE_TENS[tens]
    return total + frac


def normalize_spoken_numbers(text: str) -> str:
    """Replace English/German number words with digits (see module doc).

    Only meant for numeric checks: "one side of my face" would become
    "1 side..." if it were not for the standalone-"one" exception, so the
    phrase scan always runs on the original text.
    """
    out = _EN_NUMBER_RE.sub(lambda m: _fmt(_en_value(m.group(0))), _prep(text))
    return _DE_NUMBER_RE.sub(lambda m: _fmt(_de_value(m.group(0))), out)


# --- numeric readings ---------------------------------------------------------

_BP_PAIR_RE = re.compile(
    r"(?<![\d.,])(\d{2,3})\s*(?:/|\bover\b|\bzu\b|\b(?:ü|ue)ber\b)\s*(\d{2,3})(?![\d])", re.IGNORECASE)
_BP_SINGLE_RE = re.compile(
    r"\b(?:blood\s*pressure|bp|blutdruck\w*|systolic|systolisch\w*)\b[^\d.;!?\n]{0,25}?"
    r"(?<![\d.,])(\d{2,3})(?![\d])", re.IGNORECASE)
_GLUCOSE_RE = re.compile(
    r"\b(?:blood\s*sugar|blood\s*glucose|glucose|glukose|sugar|blutzucker\w*|zucker\w*|bz)\b"
    r"([^\d.,;!?\n]{0,30}?)(?<![\d.,])(\d{1,3}(?:[.,]\d+)?)(?![\d])", re.IGNORECASE)
_GAP_STOP_RE = re.compile(r"\b(?:and|und|but|aber|or|oder|then|dann)\b", re.IGNORECASE)
_TIME_BEFORE_RE = re.compile(
    r"\b(?:at|um|around|gegen|by|for|since|seit|f(?:ü|ue)r|every|alle)\s*$", re.IGNORECASE)
_NOT_GLUCOSE_UNIT_RE = re.compile(
    r"^\s*(?::\d|(?:hours?|hrs?|h|minutes?|mins?|stunden?|std|minuten?|years?|jahren?|jahre|times?|mal"
    r"|o'?clock|uhr|pm|a\.m|p\.m|%|percent|prozent|kg|kilos?|pounds?|lbs?|days?|tagen?|tage"
    r"|weeks?|wochen?|pills?|tablets?|tabletten?|units?|einheiten|ie)\b)",
    re.IGNORECASE)
_MMOL_RE = re.compile(r"^\s*mmol", re.IGNORECASE)
_MG_RE = re.compile(r"^\s*mg", re.IGNORECASE)


def bp_readings(text: str) -> list[tuple[int, int]]:
    """All plausible (systolic, diastolic) pairs spoken or written in text."""
    out: list[tuple[int, int]] = []
    for m in _BP_PAIR_RE.finditer(normalize_spoken_numbers(text)):
        sys_, dia = int(m.group(1)), int(m.group(2))
        if 50 <= sys_ <= 300 and 20 <= dia <= 200:
            out.append((sys_, dia))
    return out


def _bp_label(systolic: float | None, diastolic: float | None) -> str:
    if systolic is not None and diastolic is not None:
        return f"BP {_fmt(systolic)}/{_fmt(diastolic)}"
    if systolic is not None:
        return f"BP systolic {_fmt(systolic)}"
    return f"BP diastolic {_fmt(diastolic)}"


def _bp_crisis(systolic: float | None, diastolic: float | None) -> bool:
    return ((systolic is not None and systolic > settings.redflag_bp_systolic_above)
            or (diastolic is not None and diastolic > settings.redflag_bp_diastolic_above))


def _glucose_mg_dl(value: float, unit: str | None) -> float:
    if unit == "mmol" or (unit is None and value < _MMOL_AMBIGUOUS_BELOW):
        return round(value * MMOL_TO_MG_DL, 1)
    return value


def _scan_numbers(text: str) -> list[RedFlagHit]:
    hits: list[RedFlagHit] = []
    pair_spans: list[tuple[int, int]] = []
    for m in _BP_PAIR_RE.finditer(text):
        sys_, dia = int(m.group(1)), int(m.group(2))
        if not (50 <= sys_ <= 300 and 20 <= dia <= 200):
            continue
        pair_spans.append((m.start(), m.end()))
        if _bp_crisis(sys_, dia):
            hits.append(RedFlagHit("bp_crisis", _bp_label(sys_, dia), "text"))
    for m in _BP_SINGLE_RE.finditer(text):
        if any(s <= m.start(1) < e for s, e in pair_spans):
            continue  # part of a full reading, handled above
        sys_ = int(m.group(1))
        if 50 <= sys_ <= 300 and _bp_crisis(sys_, None):
            hits.append(RedFlagHit("bp_crisis", _bp_label(sys_, None), "text"))
    for m in _GLUCOSE_RE.finditer(text):
        gap, raw = m.group(1), m.group(2)
        after = text[m.end():m.end() + 24]
        if _GAP_STOP_RE.search(gap) or _TIME_BEFORE_RE.search(gap) or _NOT_GLUCOSE_UNIT_RE.match(after):
            continue
        value = float(raw.replace(",", "."))
        unit = "mmol" if _MMOL_RE.match(after) else "mg" if _MG_RE.match(after) else None
        mg_dl = _glucose_mg_dl(value, unit)
        if mg_dl < settings.redflag_glucose_below:
            label = (f"blood sugar {_fmt(mg_dl)} mg/dL" if mg_dl == value
                     else f"blood sugar {_fmt(value)} mmol/L ({_fmt(mg_dl)} mg/dL)")
            hits.append(RedFlagHit("hypoglycemia", label, "text"))
    return hits


def _dedupe(hits: list[RedFlagHit]) -> list[RedFlagHit]:
    seen: set[tuple[str, str]] = set()
    out: list[RedFlagHit] = []
    for hit in hits:
        key = (hit.category, hit.matched)
        if key not in seen:
            seen.add(key)
            out.append(hit)
    return out


# --- public API ---------------------------------------------------------------

def scan_text(text: str) -> list[RedFlagHit]:
    prepped = _prep(text)
    if not prepped.strip():
        return []
    hits = _scan_mentions(prepped) + _scan_numbers(normalize_spoken_numbers(prepped))
    return _dedupe(hits)


def _value(captured, key: str) -> float | None:
    raw = captured.get(key) if isinstance(captured, dict) else getattr(captured, key, None)
    if raw is None or isinstance(raw, bool) or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def scan_vitals(captured: CapturedData | dict | None) -> list[RedFlagHit]:
    if captured is None:
        return []
    hits: list[RedFlagHit] = []
    systolic, diastolic = _value(captured, "bp_systolic"), _value(captured, "bp_diastolic")
    if _bp_crisis(systolic, diastolic):
        hits.append(RedFlagHit("bp_crisis", _bp_label(systolic, diastolic), "vital"))
    sugar = _value(captured, "blood_sugar")
    if sugar is not None:
        mg_dl = _glucose_mg_dl(sugar, None)
        if mg_dl < settings.redflag_glucose_below:
            label = (f"blood sugar {_fmt(mg_dl)} mg/dL" if mg_dl == sugar
                     else f"blood sugar {_fmt(sugar)} mmol/L ({_fmt(mg_dl)} mg/dL)")
            hits.append(RedFlagHit("hypoglycemia", label, "vital"))
    return hits


def scan(transcript: str, captured=None) -> list[RedFlagHit]:
    patient_speech = transcripts.patient_text(transcript or "")
    return _dedupe(scan_text(patient_speech) + scan_vitals(captured))


def rule_urgency(hits: list[RedFlagHit]) -> str:
    return "urgent" if hits else "none"
