from .greenhouse import fetch_greenhouse
from .lever import fetch_lever
from .ashby import fetch_ashby
from .smartrecruiters import fetch_smartrecruiters
from .workday import fetch_workday
from .talentbrew import fetch_talentbrew
from .oleeo import fetch_oleeo
from .eightfold import fetch_eightfold
from .eightfold_pw import fetch_eightfold_pw
from .hn_hiring import fetch_hn_hiring
from .phenom import fetch_phenom
from .amazon import fetch_amazon
from .netflix import fetch_netflix
from .uber import fetch_uber
from .oracle import fetch_oracle
from .playwright_scraper import fetch_playwright

ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
    "talentbrew": fetch_talentbrew,
    "oleeo": fetch_oleeo,
    "eightfold": fetch_eightfold,
    "eightfold_pw": fetch_eightfold_pw,
    "hn_hiring": fetch_hn_hiring,
    "phenom": fetch_phenom,
    "amazon": fetch_amazon,
    "netflix": fetch_netflix,
    "uber": fetch_uber,
    "oracle": fetch_oracle,
    "playwright": fetch_playwright,
}
