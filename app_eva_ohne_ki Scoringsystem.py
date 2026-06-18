import re
import math
import pandas as pd
import streamlit as st

st.set_page_config(page_title="EVA ohne KI", page_icon="📦", layout="wide")
st.title("📦 EVA – Versandentscheidungssystem ohne KI")
st.caption("Regelbasiertes Carrier-Scoring nach MAUT/Gewichtung: Preis 35 %, Lieferzeit 28 %, Service 20 %, Sicherheit 12 %, Ausland 5 %.")

DEFAULT_WEIGHTS = {
    "price": 0.21, "insurance_efficiency": 0.14,
    "runtime": 0.20, "otd": 0.08,
    "tracking": 0.07, "damage": 0.08, "receiver_flex": 0.05,
    "liability": 0.08, "goods_fit": 0.04,
    "international": 0.05,
}

CARRIER_COLS = {"dhl": "DHL erlaubt", "dpd": "DPD erlaubt", "gls": "GLS erlaubt"}


def norm(text):
    return re.sub(r"[^a-z0-9]+", "", str(text).lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))


def find_col(df, *keywords):
    normalized_cols = {norm(c): c for c in df.columns}
    keys = [norm(k) for k in keywords]
    for ncol, original in normalized_cols.items():
        if all(k in ncol for k in keys):
            return original
    return None


def parse_number(value, default=0.0):
    if pd.isna(value):
        return default
    s = str(value).replace("€", "").replace("kg", "").replace("cm", "").replace("%", "")
    s = s.replace(".", "").replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else default


def parse_price(value):
    return parse_number(value, default=math.inf)


def parse_runtime_days(value):
    nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[,.]\d+)?", str(value))]
    if not nums:
        return 7.0
    return sum(nums) / len(nums)


def parse_weight_limit(value):
    return parse_number(value, default=0.0)


def parse_liability(value):
    s = str(value).lower()
    if "nein" in s or s.strip() in {"", "nan"}:
        return 0.0
    return parse_number(value, default=0.0)


def parse_dimensions(value):
    s = str(value).lower().replace("×", "x").replace("*", "x")
    nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[,.]\d+)?", s)]
    if not nums:
        return []
    return nums[:3] if "x" in s and len(nums) >= 3 else [max(nums)]


def package_fits(package_dims, limit_text):
    limits = parse_dimensions(limit_text)
    if not limits:
        return False
    dims = sorted(package_dims, reverse=True)
    if len(limits) == 1:
        return max(dims) <= limits[0]
    limits = sorted(limits, reverse=True)
    return all(d <= l for d, l in zip(dims, limits))


def yes(value):
    return str(value).strip().lower() in ["ja", "yes", "true", "1", "y"]


def allowed_value(value):
    s = str(value).strip().lower()
    return not (s in ["nein", "no", "false", "0", "nicht erlaubt"] or "ablehnen" in s)


def score_runtime(days):
    return max(0, min(100, (7 - days) / 6 * 100))


def score_otd(value):
    otd = parse_number(value, default=0)
    if otd >= 97: return 100
    if otd >= 90: return 75
    if otd >= 80: return 50
    return 0


def score_damage(value):
    damage = parse_number(value, default=1.5)
    if damage < 0.1: return 100
    if damage <= 0.5: return 75
    if damage <= 1.0: return 50
    return 0


def score_receiver(row, notify_col, flex_col):
    text = f"{row.get(notify_col, '')} {row.get(flex_col, '')}".lower()
    has_notify = any(x in text for x in ["ja", "benachrichtigung", "notification", "voraus"])
    has_flex = any(x in text for x in ["paketshop", "packstation", "umleitung", "abstell", "zeitfenster", "flex"])
    if has_notify and has_flex: return 100
    if has_notify or has_flex: return 50
    return 0


def insurance_cost(carrier, value, liability):
    c = str(carrier).upper()
    if value <= liability:
        return 0.0
    if c == "DHL":
        if value <= 2500: return 6.99
        if value <= 25000: return 19.99
        return math.inf
    if c == "DPD":
        if value <= 10000: return max(5.0, value * 0.01)
        return math.inf
    if c == "GLS":
        if value <= 5000: return max(5.0, value * 0.01)
        return math.inf
    return math.inf


def load_weights(xls):
    try:
        df = pd.read_excel(xls, sheet_name="Gewichtung")
        sub = find_col(df, "subkriterium")
        val = find_col(df, "normalisiert")
        if not sub or not val:
            return DEFAULT_WEIGHTS
        mapping = DEFAULT_WEIGHTS.copy()
        for _, r in df.iterrows():
            name = norm(r.get(sub, "")); w = r.get(val)
            if pd.isna(w):
                continue
            if "grundpreis" in name: mapping["price"] = float(w)
            elif "versicherung" in name: mapping["insurance_efficiency"] = float(w)
            elif "laufzeit" in name: mapping["runtime"] = float(w)
            elif "otd" in name or "lieferzuverlaessigkeit" in name: mapping["otd"] = float(w)
            elif "tracking" in name: mapping["tracking"] = float(w)
            elif "handling" in name and "warenart" not in name: mapping["damage"] = float(w)
            elif "empfaenger" in name: mapping["receiver_flex"] = float(w)
            elif "haftung" in name: mapping["liability"] = float(w)
            elif "warenart" in name: mapping["goods_fit"] = float(w)
            elif "auslandsversand" in name or "international" in name: mapping["international"] = float(w)
        total = sum(mapping.values())
        return {k: v / total for k, v in mapping.items()} if total else DEFAULT_WEIGHTS
    except Exception:
        return DEFAULT_WEIGHTS


def goods_allowed(goods_rules, goods_type, carrier):
    if goods_rules is None or goods_rules.empty:
        return True, "Keine Warenart-Regel gefunden"
    goods_col = find_col(goods_rules, "warenart")
    allow_col = find_col(goods_rules, carrier, "erlaubt")
    if not goods_col or not allow_col:
        return True, "Spalte für Warenart/Carrier fehlt"
    match = goods_rules[goods_rules[goods_col].astype(str).str.lower().str.contains(str(goods_type).lower(), na=False)]
    if match.empty:
        return True, "Keine spezifische Warenart-Regel"
    val = match.iloc[0].get(allow_col, "Ja")
    return allowed_value(val), f"Warenart-Regel: {carrier} erlaubt = {val}"


def calculate_results(xls, length, width, height, real_weight, goods_value, goods_type, dest_country):
    grund = pd.read_excel(xls, sheet_name="Grundpreis")
    try:
        goods_rules = pd.read_excel(xls, sheet_name="Sonderregeln nach Warenart")
    except Exception:
        goods_rules = pd.DataFrame()
    weights = load_weights(xls)

    cols = {
        "carrier": find_col(grund, "carrier"), "service": find_col(grund, "versandart"),
        "weight": find_col(grund, "gewicht", "max"), "dims": find_col(grund, "abmessungen"),
        "price": find_col(grund, "grundpreis"), "tracking": find_col(grund, "tracking"),
        "liability": find_col(grund, "haftung"), "runtime": find_col(grund, "laufzeit"),
        "international": find_col(grund, "auslandsversand"), "otd": find_col(grund, "otd"),
        "damage": find_col(grund, "schadensquote"), "notify": find_col(grund, "empfaengerbenachrichtigung") or find_col(grund, "empfängerbenachrichtigung"),
        "flex": find_col(grund, "zustellflexibilitaet") or find_col(grund, "zustellflexibilität"),
        "handling": find_col(grund, "handling"),
    }
    required = ["carrier", "service", "weight", "dims", "price", "tracking", "liability", "runtime", "international"]
    missing = [k for k in required if not cols[k]]
    if missing:
        st.error(f"Fehlende Pflichtspalten im Sheet Grundpreis: {missing}")
        return pd.DataFrame(), pd.DataFrame()

    package_dims = [length, width, height]
    international = str(dest_country).strip().lower() not in ["de", "deutschland", "germany"]
    rows, rejected = [], []
    best_candidates = []

    for _, r in grund.iterrows():
        carrier = str(r[cols["carrier"]]).strip().upper()
        if not carrier or carrier == "NAN":
            continue
        reasons = []
        billable_weight = real_weight
        if billable_weight > parse_weight_limit(r[cols["weight"]]):
            rejected.append([carrier, r[cols["service"]], "Gewicht überschreitet Tariflimit"]); continue
        if not package_fits(package_dims, r[cols["dims"]]):
            rejected.append([carrier, r[cols["service"]], "Maße passen nicht zum Tarif"]); continue
        if goods_value > 200 and not yes(r[cols["tracking"]]):
            rejected.append([carrier, r[cols["service"]], "Tracking fehlt bei Warenwert > 200 €"]); continue
        if international and not yes(r[cols["international"]]):
            rejected.append([carrier, r[cols["service"]], "Kein Auslandsversand für internationale Sendung"]); continue
        ok_goods, goods_reason = goods_allowed(goods_rules, goods_type, carrier)
        if not ok_goods:
            rejected.append([carrier, r[cols["service"]], goods_reason]); continue

        price = parse_price(r[cols["price"]])
        liability = parse_liability(r[cols["liability"]])
        ins = insurance_cost(carrier, goods_value, liability)
        total_cost = price + ins if math.isfinite(ins) else math.inf
        best_candidates.append((carrier, total_cost, r, price, liability, ins, goods_reason))

    # Keep cheapest valid tariff per carrier before scoring.
    best_by_carrier = {}
    for item in best_candidates:
        carrier = item[0]
        if carrier not in best_by_carrier or item[1] < best_by_carrier[carrier][1]:
            best_by_carrier[carrier] = item
    candidates = list(best_by_carrier.values())
    if not candidates:
        return pd.DataFrame(), pd.DataFrame(rejected, columns=["Carrier", "Versandart", "Ablehnungsgrund"])

    prices = [c[1] for c in candidates if math.isfinite(c[1])]
    min_price, max_price = min(prices), max(prices)

    for carrier, total_cost, r, base_price, liability, ins, goods_reason in candidates:
        price_score = 100 if max_price == min_price else (max_price - total_cost) / (max_price - min_price) * 100
        insurance_score = 100 if goods_value <= liability else max(0, min(100, liability / max(goods_value, 1) * 100))
        runtime_score = score_runtime(parse_runtime_days(r[cols["runtime"]]))
        otd_score = score_otd(r.get(cols["otd"], 0)) if cols["otd"] else 50
        tracking_score = 100 if yes(r[cols["tracking"]]) else 0
        damage_score = score_damage(r.get(cols["damage"], 1.5)) if cols["damage"] else 50
        receiver_score = score_receiver(r, cols["notify"], cols["flex"]) if cols["notify"] or cols["flex"] else 50
        liability_score = insurance_score
        handling_text = str(r.get(cols["handling"], "")).lower() if cols["handling"] else ""
        goods_fit_score = 100 if "spezial" in handling_text else 50
        intl_score = 100 if yes(r[cols["international"]]) else 50
        score = (
            weights["price"] * price_score + weights["insurance_efficiency"] * insurance_score +
            weights["runtime"] * runtime_score + weights["otd"] * otd_score +
            weights["tracking"] * tracking_score + weights["damage"] * damage_score + weights["receiver_flex"] * receiver_score +
            weights["liability"] * liability_score + weights["goods_fit"] * goods_fit_score + weights["international"] * intl_score
        )
        rows.append({
            "Carrier": carrier, "Versandart": r[cols["service"]], "Grundpreis €": round(base_price, 2),
            "Versicherung €": 0 if ins == 0 else (round(ins, 2) if math.isfinite(ins) else "Manuell"),
            "Gesamtkosten €": round(total_cost, 2) if math.isfinite(total_cost) else "Manuell",
            "Score": round(score, 2), "Preis-Score": round(price_score, 1), "Laufzeit-Score": round(runtime_score, 1),
            "Service-Score": round((tracking_score + damage_score + receiver_score) / 3, 1),
            "Sicherheits-Score": round((liability_score + goods_fit_score) / 2, 1),
            "Begründung": f"{goods_reason}; Haftung {liability:.0f} €; Laufzeit {r[cols['runtime']]}"
        })
    return pd.DataFrame(rows).sort_values("Score", ascending=False), pd.DataFrame(rejected, columns=["Carrier", "Versandart", "Ablehnungsgrund"])

uploaded = st.file_uploader("Excel-Datenbank hochladen", type=["xlsx"])
with st.sidebar:
    st.header("Sendungsdaten")
    length = st.number_input("Länge (cm)", min_value=1.0, value=40.0)
    width = st.number_input("Breite (cm)", min_value=1.0, value=30.0)
    height = st.number_input("Höhe (cm)", min_value=1.0, value=10.0)
    weight = st.number_input("Gewicht (kg)", min_value=0.1, value=2.0)
    value = st.number_input("Warenwert (€)", min_value=0.0, value=300.0)
    goods = st.text_input("Warenart", value="Elektronik")
    country = st.text_input("Zielland", value="Deutschland")

if uploaded:
    results, rejected = calculate_results(uploaded, length, width, height, weight, value, goods, country)
    if results.empty:
        st.error("Kein Carrier erfüllt alle Muss-Kriterien.")
    else:
        winner = results.iloc[0]
        st.success(f"Empfehlung: {winner['Carrier']} – {winner['Versandart']} | Score: {winner['Score']} | Kosten: {winner['Gesamtkosten €']} €")
        st.dataframe(results, use_container_width=True)
    with st.expander("Ausgeschlossene Tarife anzeigen"):
        st.dataframe(rejected, use_container_width=True)
else:
    st.info("Bitte lade die EVA-Excel-Datenbank hoch.")
