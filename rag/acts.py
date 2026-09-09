"""How a statute is named when it is cited.

A statute's abbreviation is **conventional, not derivable**. "Pakistan Penal
Code" is cited as PPC because lawyers cite it that way, and no rule over the
title produces that from those words while also refusing to produce something
from every other title.

Deriving it was tried. Reading an abbreviation out of the filename gave PPC,
CRPC and QSO correctly and, with the same confidence, gave "XIX", "XIV",
"ACTX" and "TAX" -- 515 sections were keyed to the roman numeral of their act
number. On the judgment side the same guesswork read "of", "Act" and
"Coloni[sation]" as abbreviations of statutes.

So the codes that carry the caselaw are named here, in both the forms a
judgment prints and the titles a statute prints, and the matching pattern is
built from this table so the two cannot drift apart. A statute not named here
is still chunked, still retrievable and still carries its full title -- what it
does not get is a short citation, and it takes no part in the evaluation set.
That is the correct trade: a smaller set that is right beats a larger one that
is not.
"""

from __future__ import annotations

import re

# short form -> the surface forms it is printed as, in judgments and in titles.
ACT_FORMS: dict[str, tuple[str, ...]] = {
    "PPC": ("PPC", "P.P.C.", "P.P.C", "Pakistan Penal Code"),
    "CRPC": ("CrPC", "Cr.P.C.", "Cr.P.C", "Cr.PC", "Code of Criminal Procedure"),
    "CPC": ("CPC", "C.P.C.", "C.P.C", "Code of Civil Procedure"),
    "QSO": ("QSO", "Qanun-e-Shahadat Order", "Qanun-e-Shahadat"),
    "CNSA": ("CNSA", "Control of Narcotic Substances Act"),
    "ATA": ("ATA", "Anti-Terrorism Act"),
    "NAO": ("NAO", "National Accountability Ordinance"),
}
# A printed title carries wrappers a citation does not: "THE PAKISTAN PENAL
# CODE", "THE CODE OF CIVIL PROCEDURE, 1908".
TITLE_NOISE = re.compile(r"(?i)^the\b|,?\s*\d{4}\s*$")


def normal(value: str) -> str:
    """A surface form reduced to what identifies it: letters only, upper case."""
    return re.sub(r"[^A-Za-z]", "", value).upper()


LOOKUP: dict[str, str] = {normal(form): short
                          for short, forms in ACT_FORMS.items() for form in forms}


def short_form(value: str | None) -> str | None:
    """The conventional abbreviation for an act, from a citation or a title."""
    if not value:
        return None
    return LOOKUP.get(normal(TITLE_NOISE.sub("", value.strip())))


def short_form_from_slug(slug: str | None) -> str | None:
    """The abbreviation a filename states, where the page prints no title.

    61 statutes of 982 print no title the parser can read, and among them are
    the three most cited codes in practice -- the Criminal Procedure Code, the
    Civil Procedure Code and the Qanun-e-Shahadat. Their filenames carry the
    abbreviation as a token of its own: ``code_of_criminal_procedure_crpc_1898``.

    Matched as a whole token, never as a substring. "CPC" sits inside no
    abbreviation here, but reading substrings out of names is how the earlier
    heuristic came to believe in "XIX" and "ACTX".
    """
    if not slug:
        return None
    for token in re.split(r"[^A-Za-z]+", slug):
        short = LOOKUP.get(token.upper())
        if short:
            return short
    return None


def forms_pattern() -> str:
    """One alternation over every surface form, for building a citation regex.

    Longest first, so "Cr.P.C." is preferred over the "C.P.C." that sits inside
    it -- otherwise every Criminal Procedure citation reads as a Civil
    Procedure one.
    """
    forms = sorted((form for forms in ACT_FORMS.values() for form in forms),
                   key=len, reverse=True)
    return "|".join(re.escape(form) for form in forms)


def court_name(court_id: str | None) -> str | None:
    """The court's canonical name, from the id the router already resolved.

    The printed name is not usable for this: 300 Lahore judgments print it six
    ways ("IN THE LAHORE HIGH COURT", "LAHORE HIGH COURT", "IN THE LAHORE HIGH
    COURT,"), which is noise in an embedded prefix and useless as a filter.
    """
    if not court_id:
        return None
    from specter.courts import COURTS  # noqa: PLC0415 -- avoids a package cycle

    for court in COURTS:
        if court.court_id == court_id:
            return court.name.title()
    return court_id.replace("_", " ").title()
