"""
GolfGenius Thursday Night Men's League — 2027 Season Scraper
=============================================================
Target: Target: [Update with 2027 URL]
"""

import requests
import json
import argparse
import re
import time
import os
import csv
from datetime import datetime
from bs4 import BeautifulSoup

# ── Constants ─────────────────────────────────────────────────────────────────

LEAGUE_ID  = ""  # TODO: Add 2027 League ID once available  # Corrected 2026 League Reference ID
BASE_URL   = "https://www.golfgenius.com"
WIDGET_URL = f"https://sgacc-2026thursdaynightmensleague.golfgenius.com/widgets/tournament_results"
CSV_FILE   = "2026 Thursday League Points Standings - Points Race.csv"

DIVISION_NAMES = ["Red", "Orange", "Green", "Blue", "Purple", "Black"]

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer":         "Target: [Update with 2027 URL]",
    "Accept":          "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

MANUAL_ROUNDS = []

# ── Helper Functions ──────────────────────────────────────────────────────────

def get_with_retry(session, url, params=None, headers=None, timeout=25, retries=4):
    import random
    for attempt in range(retries):
        try:
            resp = session.get(url, params=params, headers=headers or HEADERS, timeout=timeout)
            if resp.status_code == 200: return resp
            elif resp.status_code in (429, 503):
                wait = (2 ** attempt) + random.uniform(0.5, 1.5)
                time.sleep(wait)
            else: return resp
        except Exception as e:
            if attempt < retries - 1:
                wait = (2 ** attempt) + random.uniform(0.5, 1.5)
                time.sleep(wait)
            else: return None
    return None

def normalize_name(raw):
    name = re.sub(r'\s*\(\d+\)\s*$', '', raw.strip())
    if "," in name:
        last, first = name.split(",", 1)
        return f"{first.strip()} {last.strip()}"
    return name.strip()

# ── CSV Point Merger ──────────────────────────────────────────────────────────

def merge_csv_points(records):
    if not os.path.exists(CSV_FILE):
        print(f"\n  ⚠ CSV not found: '{CSV_FILE}'. Skipping team points merge.")
        return records
        
    print(f"\n  Merging team points from '{CSV_FILE}'...")
    teams = []
    with open(CSV_FILE, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 3: continue
            team_num = row[1]
            if not team_num or team_num.strip().lower() == "team #": continue
            try: float(team_num)
            except ValueError: continue
            
            team_name = row[2].strip()
            week_points = {}
            for w in range(1, 11):
                w_col = 1 + w*2
                p_col = 2 + w*2
                if p_col < len(row):
                    total = 0.0
                    try: total += float(row[w_col])
                    except: pass
                    try: total += float(row[p_col])
                    except: pass
                    if total > 0:
                        week_points[w] = total
            
            players = []
            for p in team_name.split("-"):
                p_clean = p.strip()
                last_name = p_clean.split()[-1].lower() if p_clean.split() else ""
                players.append({"full": p_clean.lower(), "last": last_name})
                
            teams.append({
                "team_name": team_name,
                "players": players,
                "week_points": week_points
            })

    matched_count = 0
    for record in records:
        player_name = record.get("player", "").lower()
        p_last = player_name.split()[-1] if player_name.split() else ""
        
        week_str = record.get("week", "")
        m = re.search(r'Week\s+(\d+)', week_str, re.IGNORECASE)
        if not m: continue
        week_num = int(m.group(1))
        
        matched_team = None
        for t in teams:
            for tp in t["players"]:
                if player_name == tp["full"] or (p_last and p_last == tp["last"]):
                    matched_team = t
                    break
            if matched_team: break
            
        if matched_team:
            record["team_name"] = matched_team["team_name"]
            pts = matched_team["week_points"].get(week_num, 0.0)
            record["match_points"] = pts / 2.0
            record["match_result"] = f"Earned {pts:g} Pts" if pts > 0 else "—"
            matched_count += 1
            
    print(f"  ✓ Successfully mapped team points for {matched_count} player rounds.")
    return records

# ── Step 1: Automatic Round Discovery ─────────────────────────────────────────

def discover_rounds(session, debug=False):
    print("Discovering rounds automatically...")
    if MANUAL_ROUNDS:
        return MANUAL_ROUNDS

    rounds = []
    # Query the core 2026 tournament widget endpoint
    target_url = f"https://www.golfgenius.com/leagues/{LEAGUE_ID}/widgets/tournament_results"
    resp = get_with_retry(session, target_url, params={"shared": "false", "no_header": ""})
    
    if resp and resp.status_code == 200:
        soup = BeautifulSoup(resp.text, "html.parser")
        for sel in soup.find_all("select"):
            for opt in sel.find_all("option"):
                val = opt.get("value", "").strip()
                txt = opt.get_text(strip=True)
                if val.isdigit() and len(val) >= 10 and txt:
                    if not any(r["id"] == val for r in rounds):
                        rounds.append({"id": val, "label": txt})
                        
    # Embedded deep-scan script fallback if standard DOM nodes fail
    if not rounds and resp:
        matches = re.findall(r'option\s+value=["\'](\d{10,22})["\'].*?>(.*?)<\/option>', resp.text, re.IGNORECASE)
        for rid, rlabel in matches:
            rlabel_clean = re.sub(r'<[^>]*>', '', rlabel).strip()
            if rid.isdigit() and rlabel_clean and not any(r["id"] == rid for r in rounds):
                rounds.append({"id": rid, "label": rlabel_clean})

    if rounds:
        print(f"  ✓ Success! Auto-discovered {len(rounds)} rounds from widgets.")
        return rounds

    print("  ⚠ Auto-discovery failed.")
    return []

# ── Step 2: Division ID Mapping ───────────────────────────────────────────────

def get_division_event_ids(session, round_id, debug=False):
    url = f"https://www.golfgenius.com/leagues/{LEAGUE_ID}/widgets/tournament_results"
    params = {"utf8": "✓", "shared": "false", "no_header": "", "round_id": round_id, "round": round_id}
    resp = get_with_retry(session, url, params=params)
    if resp is None or resp.status_code != 200: return {}
    
    divisions = {}
    
    # 2026 Client-side Fallback: Extract explicit long division keys directly out of the JS configuration map
    for dname in DIVISION_NAMES:
        pattern_json = rf'["\']id["\']?\s*:\s*["\']?(\d{{15,22}})["\']?.*?{dname}'
        m = re.search(pattern_json, resp.text, re.IGNORECASE | re.DOTALL)
        if not m:
            pattern_json_rev = rf'{dname}.*?["\']id["\']?\s*:\s*["\']?(\d{{15,22}})["\']?'
            m = re.search(pattern_json_rev, resp.text, re.IGNORECASE | re.DOTALL)
        if m:
            divisions[dname] = m.group(1)

    # Legacy HTML Fallback
    if not divisions:
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if "/v2tournaments/" in href:
                m = re.search(r"/v2tournaments/(\d+)", href)
                if m:
                    for dname in DIVISION_NAMES:
                        if dname.lower() in text.lower():
                            divisions[dname] = m.group(1)
    return divisions

# ── Step 3: Matchup Scorecard Links Extraction ───────────────────────────────

def get_matchup_links(session, event_id, debug=False):
    url = f"https://www.golfgenius.com/v2tournaments/{event_id}"
    params = {"player_stats_for_portal": "true", "round_index": "1"}
    resp = get_with_retry(session, url, params=params, headers={**HEADERS, "Referer": BASE_URL})
    if resp is None or resp.status_code != 200: return []
    
    detail_urls = []
    seen = set()

    # Match legacy HTML tables
    soup = BeautifulSoup(resp.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/tournaments2/details/" in href:
            m = re.search(r"/tournaments2/details/(\d+)", href)
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                detail_urls.append("https://www.golfgenius.com" + href)

    # Match 2026 client-side JSON configuration strings
    if not detail_urls:
        id_patterns = [
            r'["\']scorecard_id["\']?\s*:\s*["\']?(\d{6,22})["\']?',
            r'\/tournaments2\/details\/(\d{6,22})',
            r'\/scorecards\/(\d{6,22})'
        ]
        for pattern in id_patterns:
            matches = re.findall(pattern, resp.text, re.IGNORECASE)
            for match_id in matches:
                if match_id not in seen:
                    seen.add(match_id)
                    detail_urls.append(f"https://www.golfgenius.com/tournaments2/details/{match_id}")
                    
    return detail_urls

# ── Step 4: Parse Player Scorecard ────────────────────────────────────────────

def parse_scorecard(html, division_name, week_label, debug=False):
    soup = BeautifulSoup(html, "html.parser")
    records = []
    tables = soup.find_all("table")
    if not tables: return records

    table = None
    header_row = None
    total_col = None
    net_col = None
    hole_col_map = {}

    for tbl in reversed(tables):
        rows = tbl.find_all("tr")
        for row in rows:
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if "Total" in cells and "Net" in cells:
                t_col = len(cells) - 1 - cells[::-1].index("Total")
                n_col = len(cells) - 1 - cells[::-1].index("Net")
                if n_col > t_col:
                    table = tbl
                    header_row = cells
                    total_col = t_col
                    net_col = n_col
                    for i, cell in enumerate(cells):
                        if re.match(r'^(1[0-8]|[1-9])$', cell):
                            hole_col_map[int(cell)] = i
                    break
        if table is not None: break

    if not header_row: return records

    rows = table.find_all("tr")
    for row in rows:
        cells = row.find_all(["td", "th"])
        if not cells: continue

        name_raw = cells[0].get_text(strip=True)
        if not name_raw or len(name_raw) < 4: continue
        if name_raw in ("Match", "Net Score", "Total", "Net", "Out", "In"): continue
        if name_raw.startswith("1") or name_raw.startswith("2"): continue
        if re.match(r'^[\d\s●\+\-]+$', name_raw): continue
        if " vs." in name_raw or " vs " in name_raw: continue
        if re.match(r'^\d+\s*(up|&|down|halved)', name_raw, re.IGNORECASE): continue

        hdcp_match = re.search(r'\((\d+)\)', name_raw)
        handicap = hdcp_match.group(1) if hdcp_match else None
        player_name = normalize_name(name_raw)
        if not player_name or len(player_name) < 3: continue

        def cell_val(idx, allow_zero=False):
            if idx >= len(cells): return None
            raw = cells[idx].get_text(strip=True)
            raw = re.sub(r'^[●•\s]+', '', raw).strip()
            if not raw or (raw == "0" and not allow_zero): return None
            return raw

        gross = cell_val(total_col)
        net   = cell_val(net_col)

        holes = {}
        for hole_num, col_idx in hole_col_map.items():
            raw = cell_val(col_idx, allow_zero=False)
            if raw:
                try: holes[str(hole_num)] = int(raw)
                except ValueError: pass

        try:
            g = int(gross) if gross else None
            n = int(net)   if net   else None
            if g and not (25 <= g <= 75): continue
            if n is None: continue
        except (ValueError, TypeError): continue

        records.append({
            "player":     player_name,
            "week":       week_label,
            "division":   division_name,
            "handicap":   handicap,
            "gross":      gross,
            "net":        net,
            "holes":      holes,
            "scraped_at": datetime.now().isoformat(),
        })
    return records

# ── Main Control Loop ─────────────────────────────────────────────────────────

def scrape_all(player_filter=None, output_file="all_results_2026.json", save_debug=False):
    session = requests.Session()
    session.headers.update(HEADERS)

    print("Warming up session...")
    try: session.get("Target: [Update with 2027 URL]", timeout=15)
    except Exception as e: print(f"  Warning: {e}")

    rounds = discover_rounds(session, debug=save_debug)
    if not rounds:
        print("No rounds discovered. Halting execution.")
        return

    print(f"\nFound {len(rounds)} round(s) to scrape:\n")
    all_records = []

    for rnd in rounds:
        round_id   = rnd["id"]
        week_label = rnd["label"]
        print(f"\n{week_label}")

        divisions = get_division_event_ids(session, round_id, save_debug)
        if not divisions:
            print("  No divisions mapped — skipping week.")
            continue
        time.sleep(0.5)

        for dname in DIVISION_NAMES:
            event_id = divisions.get(dname)
            if not event_id: continue

            detail_urls = get_matchup_links(session, event_id, save_debug)
            print(f"  {dname:<8} {len(detail_urls)} matchups ...", end=" ", flush=True)

            div_records = []
            seen_players_this_div = set()

            for url in detail_urls:
                try:
                    resp = get_with_retry(session, url, timeout=20)
                    if resp is not None and resp.status_code == 200:
                        recs = parse_scorecard(resp.text, dname, week_label, save_debug)
                        for rec in recs:
                            key = rec["player"]
                            if key not in seen_players_this_div:
                                seen_players_this_div.add(key)
                                div_records.append(rec)
                except Exception: pass
                time.sleep(0.4)

            print(f"{len(div_records)} players")
            all_records.extend(div_records)
        time.sleep(0.8)

    # Merge external CSV data mapping before file finalization
    all_records = merge_csv_points(all_records)

    if player_filter:
        results = [r for r in all_records if player_filter.lower() in r["player"].lower()]
    else:
        results = all_records

    all_names = sorted(set(r["player"] for r in all_records))

    output = {
        "generated_at":  datetime.now().isoformat(),
        "season":        "2026",
        "player_filter": player_filter,
        "total_records": len(results),
        "notes": {
            "holes":  9,
            "format": "2-person match play",
            "league": "2026 Thursday Night Men's League",
        },
        "results": results,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    with open("all_player_names_2026.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(all_names) + "\n")

    print(f"\n{'='*65}")
    print(f"Total records : {len(all_records)}")
    print(f"Unique players: {len(all_names)}")
    print(f"  -> {output_file}")
    return output

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GolfGenius 2026 Thursday Night League Scraper")
    parser.add_argument("--player", type=str, default=None)
    parser.add_argument("--output", type=str, default="all_results_2026.json")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    scrape_all(player_filter=args.player, output_file=args.output, save_debug=args.debug)