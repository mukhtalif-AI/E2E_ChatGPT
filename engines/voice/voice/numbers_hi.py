"""numbers_hi.py - digits in a Hindi line are read out as Hindi words before the voice sees them.
The voice was trained on spoken Hindi, never on digits: "1998" came out as "उननी सो नटाठार" (Rahasya,
17 Oct). Years 1100-1999 the way people say them (उन्नीस सौ अट्ठानवे), other numbers with हज़ार / लाख /
करोड़, plus 1,00,000  12.5  40%  ₹500  10:30  1996-1998. Only the voice gets this; captions keep the digits."""
import re

ONES = ("शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस "
        "बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस "
        "तीस इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस "
        "चालीस इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास "
        "पचास इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ "
        "साठ इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर "
        "सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उन्यासी "
        "अस्सी इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी "
        "नब्बे इक्यानवे बानवे तिरानवे चौरानवे पचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे").split()
assert len(ONES) == 100


def words(n):
    """a whole number as Hindi words: 1998 -> उन्नीस सौ अट्ठानवे, 250000 -> दो लाख पचास हज़ार"""
    n = int(n)
    if n < 100:
        return ONES[n]
    if 1100 <= n <= 1999 and n % 100 != 0 or n in (1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900):
        h, r = divmod(n, 100)                      # years and prices are said in hundreds: उन्नीस सौ उनचास
        return f"{ONES[h]} सौ" + (f" {ONES[r]}" if r else "")
    out = []
    for size, name in ((10 ** 7, "करोड़"), (10 ** 5, "लाख"), (1000, "हज़ार"), (100, "सौ")):
        if n >= size:
            q, n = divmod(n, size)
            out.append(f"{words(q)} {name}")
    if n:
        out.append(ONES[n])
    return " ".join(out)


def _point(intpart, frac):
    return f"{words(intpart)} दशमलव " + " ".join(ONES[int(c)] for c in frac)


_NUM = r"\d[\d,]*(?:\.\d+)?"


def _one(s):
    s = s.replace(",", "")
    if "." in s:
        a, b = s.split(".", 1)
        return _point(a or "0", b)
    if len(s) > 1 and s.startswith("0"):          # 007, phone-like: digit by digit
        return " ".join(ONES[int(c)] for c in s)
    return words(s)


def spoken(text):
    """the line with every number written as Hindi words (text without digits comes back unchanged)"""
    t = text.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
    if not re.search(r"\d", t):
        return text
    t = re.sub(r"(\d{1,2}):(\d{2})(\s*बजे)?", lambda m: f"{words(m.group(1))} बजकर {words(m.group(2))} मिनट"
               if m.group(2) != "00" else f"{words(m.group(1))} बजे", t)
    t = re.sub(rf"({_NUM})\s*[-–—]\s*({_NUM})", lambda m: f"{_one(m.group(1))} से {_one(m.group(2))}", t)
    t = re.sub(rf"(?:₹|रु\.|Rs\.?)\s*({_NUM})", lambda m: f"{_one(m.group(1))} रुपये", t)
    t = re.sub(rf"({_NUM})\s*%", lambda m: f"{_one(m.group(1))} प्रतिशत", t)
    t = re.sub(r"(\d+)(?:st|nd|rd|th)\b", lambda m: words(m.group(1)), t)
    t = re.sub(_NUM, lambda m: _one(m.group().rstrip(",")) + ("," if m.group().endswith(",") else ""), t)
    return re.sub(r"[ \t]{2,}", " ", t)


if __name__ == "__main__":
    import sys
    for a in sys.argv[1:] or ["फिर 1996, 1997 और 1998।", "बात 1949 की है।", "₹1,50,000 और 12.5% ब्याज, 10:30 बजे"]:
        print(a, "->", spoken(a))
