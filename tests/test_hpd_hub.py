from pathlib import Path
from urllib.parse import urlparse

from housing_watch import db
from housing_watch.adapters.hpd_hub import parse_hub
from housing_watch.models import Snapshot

F = Path(__file__).parent / "fixtures" / "hpd_hub"
HTML = (F / "re-rentals.html").read_text()

# the 35 marketing agents on the hub (2026-10-18), one per playbook in the nyc-affordable-housing skill
AGENT_HOSTS = {
    "afny.org", "cgmrcompliance.com", "city5.nyc", "clintonmanagement.com", "fifthave.org",
    "housingpartnership.com", "iaffordny.com", "ihrerentals.com", "ibis.powerappsportals.com", "kgupright.com",
    "langsampropertyservices.com", "larrismarketing.com", "mgnyconsulting.com", "mutualhousingny.org",
    "mickigarciarealty.com", "mns.com", "prontohousingrentals.com", "sbmgmt.sitemanager.rentmanager.com",
    "ahgleasing.com", "residenewyork.com", "riseboro.org", "streeteasy.com", "settlementhousingfund.org",
    "sjpny.com", "soisrealestateconsulting.com", "springmanagement.net", "stnicksalliance.org",
    "east-village-homes-owner-llc.rentcafewebsite.com", "sterling23.com", "sites.google.com", "taxaceny.com",
    "tfc.com", "thebridgeny.org", "wavecrestrentals.com", "yourneighborhoodhousing.com",
}  # fmt: skip


def test_every_agent_link_and_no_nyc_gov_chrome():
    text, links = parse_hub(HTML)
    hosts = {urlparse(u).netloc.removeprefix("www.") for u in links}
    assert len(AGENT_HOSTS) == 35 and AGENT_HOSTS <= hosts
    assert {"nychdc.com", "hcr.ny.gov"} <= hosts  # the two agencies
    assert not any("nyc.gov" in h for h in hosts)  # Housing Connect, 311, footer
    assert "https://streeteasy.com/building/riverton-square" in links
    assert "Reclaim HDFC" in text and "Text-Size" not in text and "Register to vote" not in text


def test_new_agent_is_a_change(store):
    text, links = parse_hub(HTML)
    html2 = HTML.replace(
        '<li><a href="https://yourneighborhoodhousing.com/"',
        '<li><a href="https://newagent.example.com/rentals">New Agent LLC</a></li>'
        '<li><a href="https://yourneighborhoodhousing.com/"',
    )
    text2, links2 = parse_hub(html2)
    assert set(links2) - set(links) == {"https://newagent.example.com/rentals"}
    c = store
    url = "https://www.nyc.gov/hub"
    db.record_snapshots(c, "hpd_hub", [Snapshot("hpd_hub", url, url, text, links)])
    assert db.record_snapshots(c, "hpd_hub", [Snapshot("hpd_hub", url, url, text, links)]) == 0
    assert db.record_snapshots(c, "hpd_hub", [Snapshot("hpd_hub", url, url, text2, links2)]) == 1
