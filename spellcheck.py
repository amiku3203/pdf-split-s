import math
import re
import time
import unicodedata

SUFFIXES = [
    # noun/case markers (original)
    'हरूलाई', 'हरूको', 'हरूमा', 'हरूबाट', 'हरू',
    'लाई', 'बाट', 'मा', 'ले', 'को', 'का', 'की', 'ने',
    # genuine verb SUFFIXES, where stripping them leaves a real root
    # (e.g. "गर्दै" -> "गर्" + "दै", and "गर्" is a real dictionary root)
    'दै', 'दैन', 'दैनन्', 'नु', 'नुस्', 'नुहोस्',
]

# Common auxiliary/copula verb forms and highly irregular past-participles
# (होनु/हुनु "to be/become" conjugates irregularly — भएको, भयो etc. are NOT
# root+suffix, they're irregular forms, so suffix-stripping can't resolve
# them). These are complete, always-correct words, checked directly rather
# than stripped — covers the most common cases. Nepali verb morphology is
# genuinely complex; a simple suffix list can't fully solve it, so some
# rarer or less common conjugated verb forms may still be flagged even
# when they're correctly spelled. A proper fix needs a real morphological
# analyzer/stemmer, not a hand-written suffix list.
AUXILIARY_WORDS = {
    'छ', 'छु', 'छौं', 'छन्', 'छिन्', 'छैन', 'छैनन्',
    'थियो', 'थिए', 'थिइन्', 'थियौं', 'थिई',
    'हो', 'होइन', 'हुन्', 'हुन', 'भयो', 'भए', 'भएको', 'भएकी', 'भएका',
    'गरेको', 'गरेकी', 'गरेका', 'गरिएको', 'गरिएकी', 'गरिएका',
    'एको', 'एकी', 'एका',
    # irregular verbs (जानु/आउनु/दिनु) and very common function words
    'गएको', 'गएका', 'गएकी', 'आएको', 'आएका', 'आएकी', 'दिइएको', 'दिइएका', 'गर्थे', 'हुनाले',
    'कस्ता', 'कस्तो', 'यस्ता', 'यस्तो', 'एवम्', 'तपाइँ', 'तपाइँको',
    'भएर', 'भएको', 'भएका', 'भएकी', 'भइ', 'भई', 'भइरहेको', 'भइरहेका', 'भयो', 'भए', 'गई', 'गयो', 'गए',
    'हुँदा', 'हुँदै', 'जाँदा', 'जाँदै', 'छौँ', 'थिएँ', 'सं', 'वि', 'क्र',
    'हाम्रा', 'सुझाव', 'पछि', 'पनि', 'अझै', 'होलान्', 'गएर', 'आइरहेको', 'मागी', 'त्यस्ता', 'अग्ला',
    'थिइन', 'थिइनँ', 'थिएन', 'थिए', 'थियौँ', 'हौँ', 'हौं', 'औँ', 'आयो', 'आए', 'आउँछ', 'हैन', 'रही', 'राम्रा', 'र्याली', 'किमि',
    'लि', 'प्रा', 'डा', 'पृ', 'नं', 'मधेशी', 'सानै', 'मसँग', 'तिमीसँग', 'उसँग', 'हामीसँग', 'भइरहन्छ',
}

# Devanagari digits (\u0966-\u096F) are EXCLUDED here on purpose — a page
# number or year (जस्तै "२०६९") is not a spelling word and should never be
# tokenized/checked. Danda (।) and double danda are excluded too.
_DEV_WORD_RE = re.compile(r'[\u0900-\u0963\u0970-\u097F]+')


def tokenize(text: str) -> list:
    text = unicodedata.normalize('NFC', text)
    return _DEV_WORD_RE.findall(text)


# Verb endings. The dictionary only lists verbs as headwords ("गर्नु", "हुनु", "सक्नु"),
# not their conjugated forms ("गर्न", "हुन्छ", "सकिन्छ"). For a word ending in one of
# these we strip the ending and look for the root verb instead.
VERB_ENDINGS = sorted([
    'नुहोस्', 'नुपर्छ', 'नुपर्ने', 'नुपर्दैन', 'नुभयो', 'नुभएको', 'नुभएका', 'नुहुन्छ', 'ने', 'न', 'नेछ', 'नेछन्',
    'छन्', 'छौं', 'छौँ', 'छु', 'छ', 'ेको', 'ेका', 'ेकी', 'ेर', 'ेछ', 'ेपछि',
    'एको', 'एका', 'एकी', 'एर', 'एँ', 'यो', 'ई', 'दा', 'ँदा', 'ँदै',
    'िरहेको', 'िरहेका', 'िरहेकी', 'इरहेको', 'इरहेका', 'इरहेकी', 'िरहने', 'िरहन्छ', 'िरहेछ', 'िरहँदा', 'िरहेन',
    'िन्थ्यो', 'न्थ्यो', 'थ्यो', 'नुपर्ला', 'नुपर्ने', 'ेला', 'िन्थे',
    'इने', 'इन्छ', 'इएको', 'इएका', 'इए', 'ेनन्', 'ेनौँ', 'ेछैन', 'नुभए', 'इन्', 'एन', 'ेन', 'िन्', 'छिन्', 'िछिन्', 'िछ', 'थिन्', 'थे', 'ए', 'े', 'ी', 'दो', 'ँदो', 'ला', 'उला',
    'ौँ', 'ौं', 'उँ', 'ऊँ', 'ँला', 'हरू', 'ेँ',
    'िन्छन्', 'िन्छ', 'िने', 'िएको', 'िएका', 'िएकी', 'िए', 'ियो', 'ोस्', 'दै', 'दैन', 'दैनन्',
], key=len, reverse=True)

# Nouns/adjectives with attached postpositions or common compound endings.
NOUN_SUFFIXES = sorted([
    'हरूलाई', 'हरूको', 'हरूमा', 'हरूबाट', 'हरूसँग', 'हरूले', 'हरू',
    'लगायतका', 'लगायत', 'अन्तर्गत', 'सम्बन्धी', 'केन्द्रित', 'पूर्वक',
    'प्रति', 'सँग', 'समेत', 'समक्ष', 'मध्ये', 'मैत्री', 'भन्दा', 'द्वारा', 'जन्य',
    'देखि', 'सम्म', 'लाई', 'बाट', 'मा', 'ले', 'को', 'का', 'की', 'ने',
    'पछि', 'अघि', 'बारे', 'बाहेक', 'जस्तै', 'जस्तो', 'जस्ता', 'अनुसार', 'सम्बन्धित', 'नै', 'पनि', 'मात्र', 'भित्र', 'बाहिर', 'माथि',
    'मै', 'कै', 'सँगै', 'बाटै',
], key=len, reverse=True)


def load_hunspell(path_without_ext: str):
    """The Nepali Hunspell dictionary (word list + affix rules for verb/noun forms), or None
    if the files or the `spylls` package are not available."""
    try:
        from spylls.hunspell import Dictionary
        return Dictionary.from_files(path_without_ext)
    except Exception:
        return None


class SpellChecker:
    def __init__(self, valid_words: set, hunspell=None):
        self.dictionary = valid_words
        self.hunspell = hunspell
        self._hs_cache = {}

    def _hunspell_ok(self, word: str) -> bool:
        if self.hunspell is None:
            return False
        if word not in self._hs_cache:
            try:
                self._hs_cache[word] = bool(self.hunspell.lookup(word))
            except Exception:
                self._hs_cache[word] = False
        return self._hs_cache[word]

    def _verb_root_known(self, word: str) -> bool:
        for end in VERB_ENDINGS:
            if word.endswith(end) and len(word) - len(end) >= 2:
                stem = word[:-len(end)]
                roots = {stem + 'नु', stem + '्नु', stem + 'उनु'}   # दि->दिनु, गर->गर्नु, बना->बनाउनु
                if stem.endswith('ँ'):                       # आउँ + छ -> आउनु
                    roots.add(stem[:-1] + 'नु')
                if stem.endswith('न्'):             # हुन्छ -> हुन् -> हुनु
                    roots.add(stem[:-2] + 'नु')
                if any(r in self.dictionary for r in roots):
                    return True
        return False

    def is_correct(self, word: str, _depth: int = 0) -> bool:
        if word in self.dictionary or word in AUXILIARY_WORDS:
            return True
        if self._hunspell_ok(word):
            return True
        if self._verb_root_known(word):
            return True
        if word[-1:] in ('ा', 'ी') and len(word) > 3 and (word[:-1] + 'ो') in self.dictionary:
            return True                              # adjective forms: गहिरा/गहिरी, अर्का/अर्की
        if _depth < 2 and word.endswith('ै') and len(word) > 2 and self.is_correct(word[:-1], _depth + 1):
            return True                              # emphatic ै: प्रशस्तै, नजिकै, बिचबाटै
        if _depth < 2 and word.startswith('न') and len(word) > 2 and self.is_correct(word[1:], _depth + 1):
            return True                              # negative: नगर्ने, नसक्दा, नहुँदा
        if _depth == 0 and len(word) >= 6:           # compound: मानव+मूल्य, त्यस+पछि
            for i in range(3, len(word) - 2):
                if self.is_correct(word[:i], 1) and self.is_correct(word[i:], 1):
                    return True
        if _depth < 2:                               # e.g. अधिकारी + हरू + सँग
            for suf in NOUN_SUFFIXES + SUFFIXES:
                if word.endswith(suf) and len(word) - len(suf) >= 2:
                    if self.is_correct(word[:-len(suf)], _depth + 1):
                        return True
        return False


try:                                    # optional: finds suggestions up to 2 edits away
    from rapidfuzz import process as _rf_process
    from rapidfuzz.distance import DamerauLevenshtein as _DL
except ImportError:                     # without it only 1-edit suggestions are offered
    _rf_process = _DL = None


class Suggester:
    """
    Suggests the likely correct word for a word the checker flagged.
    Candidates are dictionary words (plus words the document itself uses correctly)
    that are 1 edit away (insert / delete / replace / swap a letter), then, if
    rapidfuzz is installed, up to 2 edits away. Among equals, words the document uses
    often come first - a book that says "अध्ययन" 200 times probably meant it.
    """

    def __init__(self, checker: "SpellChecker", doc_freq: dict):
        self.pool = set(checker.dictionary) | set(doc_freq)
        self.freq = doc_freq
        self.alphabet = sorted({c for w in checker.dictionary for c in w
                                if '\u0900' <= c <= '\u0963' or '\u0970' <= c <= '\u097F'})
        self._near_cache = {}
        self.deadline = float('inf')                 # set by the caller to time-box the slow search

    @staticmethod
    def _cost(ch: str) -> float:
        # a missing/extra vowel sign or halant is the most typical legacy-font slip
        return 0.5 if unicodedata.category(ch) in ("Mn", "Mc") else 1.0

    def _edits1(self, w: str) -> dict:
        """candidate -> cheapest cost, for every word one edit away from `w`."""
        out = {}

        def add(c, cost):
            if c not in out or cost < out[c]:
                out[c] = cost

        a = self.alphabet
        for i in range(len(w) + 1):
            l, r = w[:i], w[i:]
            if r:
                add(l + r[1:], self._cost(r[0]))                         # delete
                for c in a:
                    add(l + c + r[1:], max(self._cost(r[0]), self._cost(c)))   # replace
            if len(r) > 1:
                add(l + r[1] + r[0] + r[2:], 0.7)                        # swap
            for c in a:
                add(l + c + r, self._cost(c))                            # insert
        return out

    def _near_len(self, n: int, words, width: int) -> list:
        key = (id(words), n, width)                   # the big dictionary pool is reused per length
        if key not in self._near_cache:
            self._near_cache[key] = [w for w in words if abs(len(w) - n) <= width]
        return self._near_cache[key]

    def _score(self, cand: str, cost: float) -> float:
        # lower is better: edit cost, minus a bonus when the book itself uses the word
        return cost - min(0.9, 0.15 * math.log2(1 + self.freq.get(cand, 0)))

    def suggest(self, word: str, n: int = 3) -> list:
        short = len(word) <= 3                       # tiny words have too many neighbours:
        trust = (lambda c: c in self.freq) if short else (lambda c: True)   # only use the book's own words
        cost = {c: k for c, k in self._edits1(word).items()
                if c != word and len(c) > 1 and c in self.pool and trust(c)}

        if _rf_process is not None and len(word) >= 4:
            # 2 edits away, among words the book itself uses (cheap: a few thousand words)
            if not any(c in self.freq for c in cost):
                hits = _rf_process.extract(word, self._near_len(len(word), self.freq, 2),
                                           scorer=_DL.distance, score_cutoff=2, limit=10)
                for c, d, _ in hits:
                    cost.setdefault(c, float(d))
            # still nothing: 2 edits away in the whole dictionary (slow, so time-boxed)
            if not cost and len(word) >= 5 and time.time() < self.deadline:
                hits = _rf_process.extract(word, self._near_len(len(word), self.pool, 1),
                                           scorer=_DL.distance, score_cutoff=2, limit=20)
                for c, d, _ in hits:
                    cost.setdefault(c, float(d))

        return sorted(cost, key=lambda c: (self._score(c, cost[c]), c))[:n]


_JOINERS = {ord("\u200c"): None, ord("\u200d"): None}


def load_dictionary(path: str) -> set:
    # ZWJ/ZWNJ are dropped (the checker drops them from the text too), so the dictionary's
    # "पुर्‍याउनु" matches the book's "पुर्याउनु"
    with open(path, encoding="utf-8") as f:
        return {unicodedata.normalize("NFC", w.strip()).translate(_JOINERS) for w in f if w.strip()}


def check_text(text: str, checker: SpellChecker) -> dict:
    words = tokenize(text)
    mistakes = {}
    for w in words:
        if len(w) > 1 and not checker.is_correct(w):
            mistakes[w] = mistakes.get(w, 0) + 1
    return {
        "words_checked": len(words),
        "mistakes": [{"word": w, "count": c} for w, c in sorted(mistakes.items())],
    }


def pages_text_for_range(pages: list, start: int, end: int) -> str:
    """pages: [(page_no, text), ...]"""
    return "\n".join(t for n, t in pages if start <= n <= end)


def check_by_chapter(pages: list, sections: list, checker: SpellChecker) -> dict:
    """
    Groups pages into chapter -> lesson -> spellcheck-report.
    sections: [{"chapter":..., "lesson":..., "start_page":..., "end_page":...}, ...]
    """
    chapters = {}  # chapter_name -> {"lessons": {lesson_name: report}, "pages": "a-b"}

    for sec in sections:
        text = pages_text_for_range(pages, sec["start_page"], sec["end_page"])
        report = check_text(text, checker)
        report["pages"] = f"{sec['start_page']}-{sec['end_page']}"

        ch = chapters.setdefault(sec["chapter"], {"lessons": {}})
        ch["lessons"][sec["lesson"]] = report

    # roll up chapter-level totals
    result = []
    for chapter_name, data in chapters.items():
        total_words = sum(l["words_checked"] for l in data["lessons"].values())
        total_mistakes = sum(len(l["mistakes"]) for l in data["lessons"].values())
        lessons_out = [
            {"lesson": lname, **lrep} for lname, lrep in data["lessons"].items()
        ]
        result.append({
            "chapter": chapter_name,
            "total_words_checked": total_words,
            "total_unique_mistakes": total_mistakes,
            "lessons": lessons_out,
        })
    return {"chapters": result}
