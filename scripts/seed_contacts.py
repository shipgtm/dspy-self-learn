"""Submit fake contacts to the local app and wait for their workflows to finish.

Most contacts have rich profiles. A handful are deliberately hard (almost no
profile, a poor fit for the offer, or notes that tempt the model to invent
facts) so the judge produces failures and feedback for GEPA to learn from.

    uv run scripts/seed_contacts.py
    uv run scripts/seed_contacts.py --base-url http://localhost:3000 --no-wait
"""

import argparse
import json
import time
import urllib.request

CONTACTS = [
    # --- Rich profiles: should usually pass ---------------------------------
    {"name": "Dana Lee", "email": "dana.lee@northwind.example", "company": "Northwind Traders", "role": "VP of Support",
     "notes": "Hiring 10 support agents this quarter; ticket volume up 40% since launching in the EU."},
    {"name": "Marcus Chen", "email": "marcus@brightlane.example", "company": "Brightlane", "role": "Head of Customer Experience",
     "notes": "Spoke at SupportConf about reducing first-response time; uses Zendesk."},
    {"name": "Priya Raman", "email": "priya.raman@quillpay.example", "company": "QuillPay", "role": "Director of Support Operations",
     "notes": "Fintech with 24/7 support; recently posted about agent burnout on LinkedIn."},
    {"name": "Tom Okafor", "email": "tom@harborhealth.example", "company": "Harbor Health", "role": "Patient Support Manager",
     "notes": "Patient portal launch doubled inbound tickets; team of 15."},
    {"name": "Elena García", "email": "elena.garcia@fernly.example", "company": "Fernly", "role": "COO",
     "notes": "Series B e-commerce; support costs flagged as a top-3 expense in their last all-hands."},
    {"name": "Sam Whitfield", "email": "sam.w@trailmix.example", "company": "Trailmix Software", "role": "Support Team Lead",
     "notes": "Small team of 4 handling 1,200 tickets/week; asked on Reddit for triage tool recommendations."},
    {"name": "Aisha Bello", "email": "aisha@kitecloud.example", "company": "KiteCloud", "role": "VP Customer Success",
     "notes": "B2B SaaS; churn analysis showed slow support responses as a top reason."},
    {"name": "Jonas Berg", "email": "jonas.berg@polarlogistics.example", "company": "Polar Logistics", "role": "Head of Customer Service",
     "notes": "Shipping-delay tickets spike every holiday season; currently triaging manually."},
    {"name": "Mei Tanaka", "email": "mei@lumenstudio.example", "company": "Lumen Studio", "role": "Support Operations Manager",
     "notes": "Migrating from Freshdesk to Intercom next month."},
    {"name": "Carlos Mendes", "email": "carlos@ridgeway.example", "company": "Ridgeway Insurance", "role": "Director of Claims Support",
     "notes": "Claims inquiries make up 60% of tickets; exploring automation this year."},
    {"name": "Hannah Fischer", "email": "hannah.f@peakfit.example", "company": "PeakFit", "role": "Member Support Lead",
     "notes": "Fitness app with 2M users; support queue backs up every January."},
    {"name": "Ravi Patel", "email": "ravi@stackforge.example", "company": "StackForge", "role": "CTO",
     "notes": "Developer-tools startup; engineers currently rotate through support on-call."},
    {"name": "Olivia Grant", "email": "olivia.grant@meadowbank.example", "company": "Meadowbank Credit Union", "role": "Member Services Director",
     "notes": "Wants to extend support hours without adding headcount."},
    {"name": "Kwame Asante", "email": "kwame@voltride.example", "company": "VoltRide", "role": "Head of Rider Support",
     "notes": "E-scooter rentals in 12 cities; most tickets are refund requests."},
    {"name": "Sofia Rossi", "email": "sofia.rossi@vellum.example", "company": "Vellum", "role": "Customer Support Manager",
     "notes": "Recently won an award for CSAT; looking to keep quality while scaling."},
    {"name": "Liam O'Brien", "email": "liam@greenpantry.example", "company": "Green Pantry", "role": "Operations Manager",
     "notes": "Grocery delivery; support team is 8 people across two time zones."},
    {"name": "Nadia Haddad", "email": "nadia@orbitlearn.example", "company": "OrbitLearn", "role": "VP of Operations",
     "notes": "EdTech; back-to-school season triples ticket volume every August."},
    {"name": "Ben Carter", "email": "ben.carter@ironclad-hr.example", "company": "Ironclad HR", "role": "Support Director",
     "notes": "Payroll questions spike on the 1st and 15th of each month."},
    {"name": "Yuki Sato", "email": "yuki@pixelpost.example", "company": "PixelPost", "role": "Community & Support Lead",
     "notes": "Handles support for a creator platform; many tickets are duplicate bug reports."},
    {"name": "Grace Kim", "email": "grace.kim@summitbank.example", "company": "Summit Bank", "role": "SVP Customer Operations",
     "notes": "Large contact center; evaluating AI vendors in Q3 with a formal RFP."},
    {"name": "Diego Alvarez", "email": "diego@cobaltcrm.example", "company": "Cobalt CRM", "role": "Head of Support",
     "notes": "Their own product has a helpdesk add-on; support team of 20."},

    # --- Sparse profiles: hard to personalize --------------------------------
    {"name": "Chris", "email": "chris@gmail.example"},
    {"name": "J. Smith", "email": "jsmith@outlook.example"},
    {"name": "Alex Morgan", "email": "alex.morgan@mail.example", "role": "Manager"},
    {"name": "Pat Taylor", "email": "pat@company.example", "company": "Company"},

    # --- Poor fit for the offer: hard to make the value relevant -------------
    {"name": "Rosa Delgado", "email": "rosa@rosasflowers.example", "company": "Rosa's Flowers", "role": "Owner",
     "notes": "Single-location florist, no employees; takes orders by phone."},
    {"name": "Ethan Brooks", "email": "ethan.brooks@university.example", "company": "State University", "role": "Undergraduate Student",
     "notes": "Studying history; runs a chess club."},
    {"name": "Ingrid Larsen", "email": "ingrid@triagebot.example", "company": "TriageBot", "role": "CEO",
     "notes": "Founder of a direct competitor selling an AI support triage assistant."},

    # --- Tempting to invent facts ---------------------------------------------
    {"name": "Victor Hale", "email": "victor@stealth.example", "company": "Stealth Startup", "role": "Founder",
     "notes": "Company is in stealth; nothing public about the product."},
    {"name": "Mona Shah", "email": "mona.shah@bigretail.example", "company": "BigRetail", "role": "Executive",
     "notes": "Rumored to be leaving the company soon."},
]


def request(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:3000")
    parser.add_argument("--delay", type=float, default=0.5, help="seconds between submissions")
    parser.add_argument("--no-wait", action="store_true", help="submit and exit without polling")
    args = parser.parse_args()

    ids = []
    for contact in CONTACTS:
        result = request("POST", f"{args.base_url}/api/contacts", contact)
        ids.append(result["contact_id"])
        print(f"submitted #{result['contact_id']:<4} {contact['name']:<16} run {result['run_id']}")
        time.sleep(args.delay)

    if args.no_wait:
        return

    print(f"\nwaiting for {len(ids)} workflows (Ctrl-C to stop)...")
    pending = set(ids)
    results = {}
    while pending:
        time.sleep(5)
        for contact_id in sorted(pending):
            row = request("GET", f"{args.base_url}/api/contacts/{contact_id}")
            if row["status"] != "pending":
                pending.discard(contact_id)
                results[contact_id] = row
                scores = [round(a["score"], 2) for a in row["attempts"]]
                print(f"#{contact_id:<4} {row['profile']['name']:<16} {row['status']:<8} scores {scores}")

    sent = sum(r["status"] == "sent" for r in results.values())
    first_pass = sum(bool(r["attempts"]) and r["attempts"][0]["passed"] for r in results.values())
    print(f"\n{sent}/{len(results)} sent, {first_pass}/{len(results)} passed on the first attempt")
    print("stats:", json.dumps(request("GET", f"{args.base_url}/api/stats"), indent=2))


if __name__ == "__main__":
    main()
