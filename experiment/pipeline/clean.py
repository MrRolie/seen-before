"""Ticket text for the models: the issue-form boilerplate removed, so a model's limited window holds the problem.

GitHub issue forms fill tickets with fixed section headers ("### Microsoft PowerToys version"), empty answers
("_No response_"), checkbox lists and image links. They are identical across tickets and say nothing about the
problem. Only the models' input is cleaned; every arm still shows the raw ticket to Claude.
"""
import re

HEADER = re.compile(r"^#{1,6}\s.*$", re.MULTILINE)
NO_RESPONSE = re.compile(r"^_No response_\s*$", re.MULTILINE | re.IGNORECASE)
CHECKBOX = re.compile(r"^\s*- \[[ xX]\]\s.*$", re.MULTILINE)
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)|<img[^>]*>", re.IGNORECASE)
URL = re.compile(r"https?://\S+")
BLANKS = re.compile(r"\n{3,}")


def clean(body: str) -> str:
    for rx in (IMAGE, HEADER, NO_RESPONSE, CHECKBOX, URL):
        body = rx.sub("", body or "")
    return BLANKS.sub("\n\n", body).strip()
