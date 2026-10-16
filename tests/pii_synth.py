"""Seeded, realistic-looking, INVENTED values for the identifier hook's tests.
Nothing here is copied from a record and the file holds no
identifier-shaped literal: every phone, id and email is drawn from a seeded
RNG at run time, names from generic pools. The scanner reads this file like
any other.
"""

from __future__ import annotations

import random
import string

FIRST = [
    "Priya",
    "Kenji",
    "Aisha",
    "Mateo",
    "Ingrid",
    "Tomasz",
    "Nadia",
    "Oluwaseun",
    "Sofia",
    "Rahul",
    "Chloe",
    "Diego",
    "Hana",
    "Lars",
    "Amara",
    "Yusuf",
    "Elena",
    "Marcus",
    "Leilani",
    "Viktor",
]
LAST = [
    "Raman",
    "Sato",
    "Khan",
    "Alvarez",
    "Lindgren",
    "Kowalski",
    "Haddad",
    "Adeyemi",
    "Moretti",
    "Iyer",
    "Dubois",
    "Castillo",
    "Kimura",
    "Nyberg",
    "Okafor",
    "Demir",
    "Petrova",
    "Whitfield",
]
DOMAINS = ["gmail.com", "outlook.com", "yahoo.co.uk", "icloud.com", "proton.me", "hotmail.com"]
# (country code, national significant number length, leading mobile digits)
PHONE_PLANS = [
    ("852", 8, "5679"),
    ("44", 10, "7"),
    ("81", 10, "789"),
    ("1", 10, "2345678"),
    ("65", 8, "89"),
    ("61", 9, "4"),
    ("49", 11, "1"),
    ("91", 10, "6789"),
]


class Synth:
    """Seeded, realistic-looking, invented values."""

    def __init__(self, seed: int = 283):
        self.r = random.Random(seed)

    def _digits(self, n: int) -> str:
        while True:
            d = "".join(self.r.choice(string.digits) for _ in range(n))
            # avoid the detector's placeholder shapes: these must look REAL
            if len(set(d)) > 3 and not any(c * 4 in d for c in string.digits) and "123" not in d:
                return d

    def name(self) -> str:
        return f"{self.r.choice(FIRST)} {self.r.choice(LAST)}"

    def phone(self, cc: str | None = None, style: str = "e164") -> str:
        plan = next(p for p in PHONE_PLANS if p[0] == cc) if cc else self.r.choice(PHONE_PLANS)
        code, n, lead = plan
        nsn = self.r.choice(lead) + self._digits(n - 1)
        if style == "e164":
            return f"+{code}{nsn}"
        if style == "spaced":
            return f"+{code} {nsn[:4]} {nsn[4:]}"
        if style == "dashed":
            return f"+{code}-{nsn[:4]}-{nsn[4:]}"
        if style == "local":
            return nsn
        if style == "jid":
            return f"{code}{nsn}@s.whatsapp.net"
        raise ValueError(style)

    def email(self, name: str | None = None) -> str:
        first, last = (name or self.name()).lower().split(" ", 1)
        sep = self.r.choice([".", "_", ""])
        return f"{first}{sep}{last}{self.r.randint(7, 98)}" + "@" + self.r.choice(DOMAINS)

    def snowflake(self) -> str:
        return self.r.choice("1234") + self._digits(17)

    def uuid(self) -> str:
        h = "".join(self.r.choice("0123456789abcdef") for _ in range(32))
        h = h[:12] + "4" + h[13:16] + self.r.choice("89ab") + h[17:]
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"

    def signal_group(self) -> str:
        alphabet = string.ascii_letters + string.digits + "+/"
        while True:
            s = "".join(self.r.choice(alphabet) for _ in range(43))
            if (
                any(c.isupper() for c in s)
                and any(c.islower() for c in s)
                and any(c.isdigit() for c in s)
            ):
                return s + "="

    def base_point(self) -> tuple[float, float]:
        return round(self.r.uniform(-50, 60), 6), round(self.r.uniform(-170, 170), 6)

    def near(self, p: tuple[float, float], metres: float = 40.0) -> tuple[float, float]:
        d = metres / 111_000
        return round(p[0] + self.r.uniform(-d, d), 6), round(p[1] + self.r.uniform(-d, d), 6)
