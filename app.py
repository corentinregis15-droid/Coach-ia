
import streamlit as st
import sqlite3, json, math, io, xml.etree.ElementTree as ET
from datetime import datetime, date
from pathlib import Path
import pandas as pd
import numpy as np

DB=Path("coach_ai_adaptive_v6.db")

# ---------- DB ----------
def db():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

def init_db():
    c=db()
    c.execute("""CREATE TABLE IF NOT EXISTS athletes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        age INTEGER, sex TEXT, weight REAL, height REAL,
        hr_max INTEGER, hr_rest INTEGER,
        vma REAL, critical_speed REAL, ftp REAL,
        weekly_sessions INTEGER, weekly_km REAL,
        goal_type TEXT, goal_name TEXT, goal_date TEXT,
        weaknesses_json TEXT, pb_json TEXT, strength_json TEXT, erg_json TEXT,
        created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS plans(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        athlete_id INTEGER,
        created_at TEXT,
        goal_type TEXT, goal_name TEXT, goal_date TEXT,
        plan_json TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS sessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        athlete_id INTEGER,
        plan_id INTEGER,
        plan_week INTEGER,
        session_date TEXT,
        sport TEXT,
        title TEXT,
        objective TEXT,
        planned_json TEXT,
        actual_json TEXT,
        rpe INTEGER,
        analysis TEXT,
        adjustment_json TEXT,
        created_at TEXT
    )""")
    c.commit(); c.close()

# ---------- Helpers ----------
def fmt_pace(sec):
    if sec is None:return "-"
    try:
        if np.isnan(sec):return "-"
    except: pass
    m=int(sec//60); s=int(round(sec%60))
    if s==60:m+=1;s=0
    return f"{m}:{s:02d}/km"

def pace_from_kmh(kmh):
    return 3600/kmh if kmh and kmh>0 else None

def sec_per_km_from_mps(mps):
    return 1000/mps if mps and mps>0 else None

def weeks_until(d):
    return max(1, math.ceil((d-date.today()).days/7))

# ---------- Parsers ----------
def parse_fit(file):
    from fitparse import FitFile
    fit=FitFile(file)
    recs=[{f.name:f.value for f in r} for r in fit.get_messages("record")]
    laps=[{f.name:f.value for f in r} for r in fit.get_messages("lap")]
    return pd.DataFrame(recs), pd.DataFrame(laps)

def ends(el,suffix):
    return el.tag.lower().endswith(suffix.lower())

def parse_gpx(file):
    root=ET.parse(file).getroot()
    rows=[]
    for pt in root.iter():
        if ends(pt,"trkpt"):
            row={}
            for ch in pt.iter():
                tag=ch.tag.lower(); txt=(ch.text or "").strip()
                if not txt: continue
                try:
                    if tag.endswith("time"): row["timestamp"]=txt
                    elif tag.endswith("ele"): row["altitude"]=float(txt)
                    elif tag.endswith("hr"): row["heart_rate"]=float(txt)
                    elif tag.endswith("cad"): row["cadence"]=float(txt)
                    elif tag.endswith("power") or tag.endswith("watts"): row["power"]=float(txt)
                except: pass
            rows.append(row)
    return pd.DataFrame(rows), pd.DataFrame()

def parse_tcx(file):
    root=ET.parse(file).getroot()
    rows=[]
    for tp in root.iter():
        if ends(tp,"Trackpoint"):
            row={}
            for ch in tp.iter():
                tag=ch.tag.lower(); txt=(ch.text or "").strip()
                if not txt: continue
                try:
                    if tag.endswith("time"): row["timestamp"]=txt
                    elif tag.endswith("distancemeters"): row["distance"]=float(txt)
                    elif tag.endswith("altitudemeters"): row["altitude"]=float(txt)
                    elif tag.endswith("cadence"): row["cadence"]=float(txt)
                    elif tag.endswith("watts"): row["power"]=float(txt)
                    elif tag.endswith("speed"): row["speed"]=float(txt)
                except: pass
            for hrnode in tp.iter():
                if ends(hrnode,"HeartRateBpm"):
                    for sub in hrnode.iter():
                        if ends(sub,"Value"):
                            try: row["heart_rate"]=float((sub.text or "").strip())
                            except: pass
            rows.append(row)
    return pd.DataFrame(rows), pd.DataFrame()

def parse_csv(file):
    raw=file.read()
    try: text=raw.decode("utf-8")
    except: text=raw.decode("latin1")
    df=pd.read_csv(io.StringIO(text))
    mp={}
    for c in df.columns:
        lc=c.lower().strip()
        if lc in ["time","timestamp","datetime"]: mp[c]="timestamp"
        elif lc in ["hr","heart rate","heart_rate","heartrate"]: mp[c]="heart_rate"
        elif lc in ["power","watts"]: mp[c]="power"
        elif lc in ["cadence","rpm"]: mp[c]="cadence"
        elif lc in ["distance","distance_m","meters"]: mp[c]="distance"
        elif lc in ["speed","speed_m_s","velocity"]: mp[c]="speed"
        elif lc in ["altitude","elevation"]: mp[c]="altitude"
    return df.rename(columns=mp), pd.DataFrame()

def parse_activity(upload):
    ext=upload.name.lower().split(".")[-1]
    upload.seek(0)
    if ext=="fit":
        r,l=parse_fit(upload); return r,l,"FIT"
    if ext=="tcx":
        r,l=parse_tcx(upload); return r,l,"TCX"
    if ext=="gpx":
        r,l=parse_gpx(upload); return r,l,"GPX"
    if ext=="csv":
        r,l=parse_csv(upload); return r,l,"CSV"
    raise ValueError("Format non pris en charge")

# ---------- Metrics ----------
def summarize_activity(df):
    out={}
    if df is None or df.empty:return out
    d=df.copy()

    if "timestamp" in d:
        d["timestamp"]=pd.to_datetime(d["timestamp"],errors="coerce")
        t=d["timestamp"].dropna()
        if len(t)>1:
            out["duration_min"]=round((t.max()-t.min()).total_seconds()/60,1)

    if "distance" in d:
        x=pd.to_numeric(d["distance"],errors="coerce").dropna()
        if len(x):
            mx=float(x.max())
            out["distance_km"]=round(mx/1000 if mx>100 else mx,2)

    if "speed" in d:
        x=pd.to_numeric(d["speed"],errors="coerce").dropna()
        x=x[x>0.5]
        if len(x):
            mps=float(x.mean()) if x.mean()<15 else float(x.mean()/3.6)
            out["speed_avg_kmh"]=round(mps*3.6,2)
            out["pace_avg_sec"]=round(1000/mps,1)
    elif out.get("distance_km") and out.get("duration_min"):
        kmh=out["distance_km"]/(out["duration_min"]/60)
        out["speed_avg_kmh"]=round(kmh,2)
        out["pace_avg_sec"]=round(3600/kmh,1)

    if "heart_rate" in d:
        x=pd.to_numeric(d["heart_rate"],errors="coerce").dropna()
        if len(x):
            out["hr_avg"]=round(float(x.mean()),1)
            out["hr_max"]=int(x.max())

    if "cadence" in d:
        x=pd.to_numeric(d["cadence"],errors="coerce").dropna()
        if len(x):
            avg=float(x.mean())
            out["cadence_avg"]=round(avg*2 if avg<120 else avg,1)

    if "power" in d:
        x=pd.to_numeric(d["power"],errors="coerce").dropna()
        if len(x):
            out["power_avg"]=round(float(x.mean()),1)
            out["power_max"]=round(float(x.max()),1)
            out["power_cv_pct"]=round(float(x.std()/x.mean()*100),1) if x.mean() else None

    if "altitude" in d:
        x=pd.to_numeric(d["altitude"],errors="coerce").dropna()
        if len(x)>1:
            g=np.diff(x)
            out["elev_gain"]=round(float(g[g>0].sum()),0)

    if "heart_rate" in d and "speed" in d:
        tmp=d[["heart_rate","speed"]].copy()
        tmp["heart_rate"]=pd.to_numeric(tmp["heart_rate"],errors="coerce")
        tmp["speed"]=pd.to_numeric(tmp["speed"],errors="coerce")
        tmp=tmp.dropna()
        tmp=tmp[(tmp.heart_rate>80)&(tmp.speed>0.5)]
        if len(tmp)>40:
            m=len(tmp)//2
            a=tmp.iloc[:m]; b=tmp.iloc[m:]
            r1=a.speed.mean()/a.heart_rate.mean()
            r2=b.speed.mean()/b.heart_rate.mean()
            out["decoupling_pct"]=round((r1-r2)/r1*100,1) if r1 else None

    return out

def analyze_laps(laps):
    if laps is None or laps.empty:return pd.DataFrame()
    rows=[]
    for _,r in laps.iterrows():
        dur=r.get("total_elapsed_time",r.get("total_timer_time",None))
        dist=r.get("total_distance",None)
        speed=r.get("avg_speed",r.get("enhanced_avg_speed",None))
        try:
            if (speed is None or pd.isna(speed)) and dur and dist:
                speed=dist/dur
        except: pass
        pace=sec_per_km_from_mps(speed) if speed and speed>0 else None
        rows.append({
            "Lap":len(rows)+1,
            "Durée (s)":round(float(dur),1) if dur is not None and not pd.isna(dur) else None,
            "Distance (m)":round(float(dist),0) if dist is not None and not pd.isna(dist) else None,
            "Allure":fmt_pace(pace) if pace else "-",
            "Allure_sec":pace,
            "FC moy":r.get("avg_heart_rate",None),
            "FC max":r.get("max_heart_rate",None),
            "Cadence":r.get("avg_running_cadence",r.get("avg_cadence",None)),
            "Puissance":r.get("avg_power",None),
        })
    return pd.DataFrame(rows)

def detect_work_laps(lapdf):
    if lapdf.empty:return lapdf
    p=pd.to_numeric(lapdf["Allure_sec"],errors="coerce")
    valid=p.dropna()
    x=lapdf.copy()
    if len(valid)<3:
        x["Type"]="Lap"; return x
    med=valid.median()
    x["Type"]=np.where(p<med*0.92,"Travail",np.where(p>med*1.08,"Récup","Intermédiaire"))
    return x

def interval_metrics(lapdf):
    if lapdf.empty or "Type" not in lapdf:return {}
    work=lapdf[lapdf["Type"]=="Travail"]
    if work.empty:return {}
    p=pd.to_numeric(work["Allure_sec"],errors="coerce").dropna()
    out={"n_work":len(work)}
    if len(p):
        out["work_pace_avg"]=float(p.mean())
        out["pace_spread_pct"]=round((p.max()-p.min())/p.mean()*100,1)
        if len(p)>1:
            out["first_last_change_pct"]=round((p.iloc[-1]-p.iloc[0])/p.iloc[0]*100,1)
    hr=pd.to_numeric(work["FC moy"],errors="coerce").dropna()
    if len(hr)>1:
        out["work_hr_rise"]=round(float(hr.iloc[-1]-hr.iloc[0]),1)
    return out

# ---------- Planning ----------
def weakness_priority(goal_type, weaknesses):
    w=[x.lower() for x in weaknesses]
    if goal_type=="HYROX":
        order=["Sled Push","Sled Pull","Wall Balls","Running compromis","SkiErg","RowErg","Force","Endurance musculaire"]
    else:
        order=["Endurance","Seuil","VO₂max","Vitesse","Économie de course","Sortie longue"]
    p=[x for x in order if x.lower() in w]
    return p[:3] if p else order[:3]

def distribute_sessions(n,goal_type):
    if goal_type=="RUN":
        if n<=3:return ["Qualité 1","Endurance","Sortie longue"][:n]
        if n==4:return ["Qualité 1","Endurance","Qualité 2","Sortie longue"]
        if n==5:return ["Qualité 1","Endurance","Qualité 2","Endurance","Sortie longue"]
        return ["Qualité 1","Endurance","Qualité 2","Endurance","Footing + lignes droites","Sortie longue"]+["Endurance"]*(n-6)
    else:
        if n<=3:return ["Run qualité","Force / stations","Conditioning HYROX"][:n]
        if n==4:return ["Run qualité","Upper / stations","Lower / stations","Conditioning HYROX"]
        if n==5:return ["Run qualité","Upper / stations","Endurance run","Lower / stations","Conditioning HYROX"]
        return ["Run qualité","Upper / stations","Endurance run","Lower / stations","Run spécifique","Conditioning HYROX"]+["Endurance / récup"]*(n-6)

def run_session(role,block,a,weakness):
    vma=float(a.get("vma") or 0)
    cs=float(a.get("critical_speed") or 0)
    p100=pace_from_kmh(vma) if vma else None
    p90=pace_from_kmh(vma*.90) if vma else None
    pcs=pace_from_kmh(cs) if cs else None
    psemi=pace_from_kmh(cs*.94) if cs else None
    p10=pace_from_kmh(vma*.92) if vma else None
    g=(a.get("goal_name") or "").lower()

    if role=="Endurance":
        return "Footing endurance","45–70 min facile."
    if role=="Footing + lignes droites":
        return "Footing + lignes droites","45–55 min facile + 6–8 × 12–15 s rapides."
    if role=="Sortie longue":
        if block=="Spécifique" and "semi" in g:
            return "Sortie longue spécifique",f"1h40–2h dont 2 × 15–20 min autour de {fmt_pace(psemi)}." if psemi else "1h40–2h dont blocs allure semi."
        return "Sortie longue","75–120 min facile."
    if role=="Qualité 1":
        if block=="Base":
            return "LT1 / tempo bas",f"3 × 10 min autour de {fmt_pace(p90)}, récup 2 min." if p90 else "3 × 10 min tempo contrôlé."
        if block=="Développement":
            return "Seuil",f"3 × 10 min autour de {fmt_pace(pcs)}, récup 2 min." if pcs else "3 × 10 min au seuil."
        if block=="Spécifique":
            if "semi" in g:return "Allure semi",f"3 × 3 km autour de {fmt_pace(psemi)}, récup 2'30." if psemi else "3 × 3 km allure semi."
            if "10" in g:return "Allure 10 km",f"5 × 1200 m autour de {fmt_pace(p10)}, récup 1'30." if p10 else "5 × 1200 m allure 10 km."
            return "Spécifique course","Blocs à allure objectif."
        return "Rappel intensité","3 × 1 km allure objectif."
    if role=="Qualité 2":
        if "vo₂max" in weakness.lower() or block=="Développement":
            return "VO₂max",f"5 × 4 min autour de {fmt_pace(p100)}, récup 2'30." if p100 else "5 × 4 min VO₂max."
        return "Seuil / tempo",f"4 × 8 min autour de {fmt_pace(pcs)}, récup 90 s." if pcs else "4 × 8 min seuil."
    return "Footing facile","40–60 min facile."

def hyrox_session(role,block,a,weaknesses):
    w=[x.lower() for x in weaknesses]
    cs=float(a.get("critical_speed") or 0)
    vma=float(a.get("vma") or 0)
    phyrox=pace_from_kmh(cs*.93) if cs else pace_from_kmh(vma*.88) if vma else None

    if role=="Run qualité":
        if block=="Base":return "Seuil run","3 × 10 min contrôlé, récup 2 min."
        if block=="Développement":return "Run qualité",f"6 × 1 km autour de {fmt_pace(phyrox)}, récup 1 min." if phyrox else "6 × 1 km allure HYROX."
        if block=="Spécifique":return "Run compromis",f"5 × (1 km autour de {fmt_pace(phyrox)} + 60–90 s station)." if phyrox else "5 × (1 km + station)."
        return "Rappel run","4 × 1 km allure course."
    if role=="Endurance run":
        return "Endurance run","50–70 min facile."
    if role=="Run spécifique":
        return "Run HYROX spécifique",f"6–8 × 1 km autour de {fmt_pace(phyrox)}." if phyrox else "6–8 × 1 km allure HYROX."
    if role=="Upper / stations":
        focus=[x for x in ["Sled Pull","Wall Balls","SkiErg"] if x.lower() in w] or ["Sled Pull","Wall Balls"]
        return "Upper + stations",f"Bench entretien + tirage horizontal lourd + push press + {', '.join(focus)} + grip/tronc."
    if role=="Lower / stations":
        focus=[x for x in ["Sled Push","Force"] if x.lower() in w] or ["Sled Push","Force"]
        if block=="Base":return "Lower force",f"Squat 4–5 × 3 + split squat + travail lourd {', '.join(focus)}."
        if block=="Développement":return "Lower puissance",f"Squat 4 × 3 + contrast jump + travail vitesse {', '.join(focus)}."
        return "Lower spécifique",f"Rappel force 3 × 2 + travail charge course {', '.join(focus)}."
    if role=="Conditioning HYROX":
        if block=="Base":return "Flow HYROX","30–40 min RPE 5–6, technique et transitions."
        if block=="Développement":return "Conditioning spécifique","4–5 tours : station prioritaire + run/erg, RPE 7."
        if block=="Spécifique":return "Simulation partielle","40–60 min, 4 à 6 blocs proches course."
        return "Flow léger","20–30 min facile."
    if role=="Force / stations":
        return "Force + stations","Squat/deadlift lourd + 1 station prioritaire."
    return "Récupération","40–50 min facile."

def build_plan(a):
    goal_type=a["goal_type"]
    total=weeks_until(pd.to_datetime(a["goal_date"]).date())
    n=int(a["weekly_sessions"] or 4)
    weaknesses=json.loads(a["weaknesses_json"] or "[]")
    priorities=weakness_priority(goal_type,weaknesses)

    if total<=6:
        structure=[("Spécifique",max(1,total-1)),("Affûtage",1)]
    elif total<=10:
        structure=[("Base",2),("Développement",3),("Spécifique",max(2,total-6)),("Affûtage",1)]
    else:
        base=max(3,round(total*.28)); dev=max(3,round(total*.27)); taper=2
        spec=max(3,total-base-dev-taper)
        structure=[("Base",base),("Développement",dev),("Spécifique",spec),("Affûtage",taper)]

    plan=[]; wk=1
    for bname,dur in structure:
        if goal_type=="RUN":
            bp={"Base":["Endurance","Économie de course"]+priorities[:1],
                "Développement":["Seuil","VO₂max"]+priorities[:1],
                "Spécifique":["Allure objectif"]+priorities[:2],
                "Affûtage":["Fraîcheur","Maintien intensité"]}[bname]
        else:
            bp={"Base":["Force générale","Technique stations","Base run"]+priorities[:1],
                "Développement":["Puissance","Run qualité"]+priorities[:2],
                "Spécifique":["Transfert HYROX","Run compromis"]+priorities[:2],
                "Affûtage":["Fraîcheur","Vitesse stations"]}[bname]

        block={"name":bname,"duration":dur,"priorities":bp,"weeks":[]}
        roles=distribute_sessions(n,goal_type)

        for i in range(dur):
            deload=(dur>=4 and i==dur-1 and bname!="Affûtage")
            sessions=[]
            for role in roles:
                if goal_type=="RUN":
                    title,desc=run_session(role,bname,a,priorities[0] if priorities else "")
                else:
                    title,desc=hyrox_session(role,bname,a,weaknesses)
                if deload: desc="Version allégée (-20 à -30 % volume). "+desc
                sessions.append({"role":role,"title":title,"description":desc})

            block["weeks"].append({
                "week":wk,
                "focus":bp[min(i,len(bp)-1)],
                "deload":deload,
                "sessions":sessions
            })
            wk+=1
        plan.append(block)
    return plan

# ---------- Coaching / adaptation ----------
def analyze_session(planned,summary,intervals,rpe):
    notes=[]
    verdict="Séance maîtrisée"

    if rpe>=9:
        verdict="Séance très coûteuse"
        notes.append("RPE très élevé : ne pas augmenter la charge immédiatement.")
    elif rpe<=6:
        notes.append("Coût perceptif contenu : marge potentielle.")

    if summary.get("pace_avg_sec") is not None:
        notes.append(f"Allure moyenne globale : **{fmt_pace(summary['pace_avg_sec'])}**.")
    if summary.get("hr_avg") is not None:
        notes.append(f"FC moyenne : **{summary['hr_avg']} bpm**, max **{summary.get('hr_max','-')} bpm**.")
    if summary.get("decoupling_pct") is not None:
        d=summary["decoupling_pct"]
        if abs(d)<5: notes.append(f"Découplage allure/FC faible (**{d} %**) : bonne stabilité aérobie.")
        elif d<10: notes.append(f"Découplage modéré (**{d} %**).")
        else: notes.append(f"Découplage élevé (**{d} %**) : coût croissant au fil de la séance.")

    if intervals.get("n_work"):
        notes.append(f"**{intervals['n_work']} fractions** détectées.")
        notes.append(f"Allure moyenne des fractions : **{fmt_pace(intervals.get('work_pace_avg'))}**.")
        sp=intervals.get("pace_spread_pct")
        if sp is not None:
            if sp<=3: notes.append(f"Régularité excellente (**{sp} %** de dispersion).")
            elif sp<=6: notes.append(f"Régularité correcte (**{sp} %**).")
            else: notes.append(f"Dispersion importante (**{sp} %**) : pacing ou intensité à revoir.")
        fl=intervals.get("first_last_change_pct")
        if fl is not None:
            if fl>3: notes.append("La dernière fraction est nettement plus lente : fatigue périphérique ou départ trop rapide probable.")
            elif fl<-3: notes.append("Fin plus rapide : départ probablement prudent.")
            else: notes.append("Allure bien conservée jusqu'à la fin.")

    action="maintain"; factor=1.0
    if rpe>=9:
        action="reduce"; factor=0.8
    elif rpe<=6 and (intervals.get("pace_spread_pct") is None or intervals.get("pace_spread_pct",99)<=3):
        action="progress"; factor=1.05

    report=f"""## Verdict
**{verdict}**

## Analyse coach
"""+"\n".join(f"- {x}" for x in notes)+f"""

## Décision
**{action}**

Facteur de volume proposé pour la prochaine séance comparable : **{factor}**
"""
    return report,{"action":action,"volume_factor":factor}

def adjust_plan(plan,current_week,adjustment):
    p=json.loads(json.dumps(plan))
    action=adjustment["action"]
    for b in p:
        for w in b["weeks"]:
            if w["week"]==current_week+1:
                if action=="reduce":
                    w["adjustment_note"]="Réduire d'environ 20 % le volume de la prochaine séance clé. Conserver l'intensité cible."
                elif action=="progress":
                    w["adjustment_note"]="Progression modérée possible : +5 % de volume sur une seule séance clé, sans augmenter simultanément l'intensité."
                else:
                    w["adjustment_note"]="Maintenir le contenu prévu."
    return p

# ---------- UI ----------
init_db()
st.set_page_config(page_title="Coach IA Adaptatif V6",layout="wide")
st.title("Coach IA — Adaptatif V6")
st.caption("RUN / HYROX · plan complet · import FIT/TCX/GPX/CSV · analyse · adaptation")

tabs=st.tabs(["Athlètes","Créer le plan","Plan actuel","Analyser séance","Historique"])

with tabs[0]:
    st.subheader("Profil complet")
    with st.form("ath"):
        a,b,c=st.columns(3)
        name=a.text_input("Nom / prénom")
        age=a.number_input("Âge",10,100,30)
        sex=a.selectbox("Sexe",["Non renseigné","Femme","Homme","Autre"])
        weight=b.number_input("Poids (kg)",30.,200.,70.,0.1)
        height=b.number_input("Taille (cm)",100.,230.,175.,1.)
        hrmax=b.number_input("FC max",100,230,190)
        hrrest=c.number_input("FC repos",25,120,50)
        vma=c.number_input("VMA (km/h)",0.,30.,0.,0.1)
        cs=c.number_input("Vitesse critique (km/h)",0.,30.,0.,0.1)
        ftp=c.number_input("FTP vélo (W)",0.,600.,0.,1.)

        st.markdown("### Objectif")
        goal_type=st.selectbox("Type de course",["RUN","HYROX"])
        goal_name=st.text_input("Course / objectif","Semi-marathon")
        goal_date=st.date_input("Date de course",date.today())
        weekly_sessions=st.number_input("Nombre de séances possibles / semaine",1,12,5)
        weekly_km=st.number_input("Volume run actuel (km/semaine)",0.,250.,40.,1.)

        st.markdown("### Points à travailler")
        run_weak=["Endurance","Seuil","VO₂max","Vitesse","Économie de course","Sortie longue"]
        hyrox_weak=["Sled Push","Sled Pull","Wall Balls","Running compromis","SkiErg","RowErg","Force","Endurance musculaire"]
        weaknesses=st.multiselect("Points faibles / priorités",hyrox_weak if goal_type=="HYROX" else run_weak)

        st.markdown("### Records")
        x,y,z=st.columns(3)
        pb5=x.text_input("PB 5 km")
        pb10=x.text_input("PB 10 km")
        pbhm=y.text_input("PB semi")
        pbm=y.text_input("PB marathon")
        pbhyrox=z.text_input("PB HYROX")

        st.markdown("### Force / ergos")
        x,y,z=st.columns(3)
        squat=x.number_input("Squat (kg)",0.,400.,0.)
        dead=x.number_input("Deadlift (kg)",0.,400.,0.)
        pushpress=y.number_input("Push press (kg)",0.,300.,0.)
        ski2k=y.text_input("SkiErg 2 km")
        row2k=z.text_input("RowErg 2 km")

        if st.form_submit_button("Enregistrer l'athlète") and name:
            pb={"5k":pb5,"10k":pb10,"semi":pbhm,"marathon":pbm,"hyrox":pbhyrox}
            strength={"squat":squat,"deadlift":dead,"pushpress":pushpress}
            erg={"ski2k":ski2k,"row2k":row2k}
            c0=db()
            c0.execute("""INSERT INTO athletes(name,age,sex,weight,height,hr_max,hr_rest,vma,critical_speed,ftp,weekly_sessions,weekly_km,goal_type,goal_name,goal_date,weaknesses_json,pb_json,strength_json,erg_json,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name,age,sex,weight,height,hrmax,hrrest,vma,cs,ftp,weekly_sessions,weekly_km,goal_type,goal_name,str(goal_date),json.dumps(weaknesses),json.dumps(pb),json.dumps(strength),json.dumps(erg),datetime.now().isoformat()))
            c0.commit();c0.close()
            st.success("Athlète enregistré.")

    c0=db()
    ats=pd.read_sql_query("SELECT id,name,goal_type,goal_name,goal_date,vma,critical_speed,weekly_sessions,weekly_km FROM athletes ORDER BY id DESC",c0)
    c0.close()
    if not ats.empty: st.dataframe(ats,use_container_width=True)

with tabs[1]:
    c0=db(); ats=pd.read_sql_query("SELECT * FROM athletes ORDER BY name",c0); c0.close()
    if ats.empty:
        st.info("Crée d'abord un athlète.")
    else:
        aid=st.selectbox("Athlète",ats["id"],format_func=lambda x:ats.loc[ats.id==x,"name"].iloc[0],key="planath")
        a=ats[ats.id==aid].iloc[0].to_dict()
        st.write(f"Objectif : **{a['goal_type']} — {a['goal_name']}**")
        st.write(f"Date : **{a['goal_date']}** · **{a['weekly_sessions']} séances/semaine**")
        st.write("Priorités :",", ".join(json.loads(a["weaknesses_json"] or "[]")) or "aucune")

        if st.button("Créer toute la préparation jusqu'au jour J"):
            plan=build_plan(a)
            c0=db()
            c0.execute("INSERT INTO plans(athlete_id,created_at,goal_type,goal_name,goal_date,plan_json) VALUES(?,?,?,?,?,?)",
                       (int(aid),datetime.now().isoformat(),a["goal_type"],a["goal_name"],a["goal_date"],json.dumps(plan)))
            c0.commit();c0.close()
            st.success("Plan créé.")

with tabs[2]:
    c0=db()
    plans=pd.read_sql_query("""SELECT p.id,a.name,p.goal_name,p.plan_json FROM plans p JOIN athletes a ON a.id=p.athlete_id ORDER BY p.id DESC""",c0)
    c0.close()
    if plans.empty:
        st.info("Aucun plan.")
    else:
        pid=st.selectbox("Plan",plans["id"],format_func=lambda x:f"{plans.loc[plans.id==x,'name'].iloc[0]} — {plans.loc[plans.id==x,'goal_name'].iloc[0]}")
        plan=json.loads(plans.loc[plans.id==pid,"plan_json"].iloc[0])
        for b in plan:
            with st.expander(f"{b['name']} — {b['duration']} semaine(s)",expanded=True):
                st.write("Priorités :"," · ".join(b["priorities"]))
                for w in b["weeks"]:
                    st.markdown(f"### Semaine {w['week']} — {w['focus']}")
                    if w.get("adjustment_note"): st.warning(w["adjustment_note"])
                    for s in w["sessions"]:
                        st.write(f"**{s['role']} — {s['title']}** : {s['description']}")

with tabs[3]:
    c0=db()
    plans=pd.read_sql_query("""SELECT p.id,p.athlete_id,a.name,p.goal_name,p.plan_json
    FROM plans p JOIN athletes a ON a.id=p.athlete_id ORDER BY p.id DESC""",c0)
    c0.close()

    if plans.empty:
        st.info("Crée d'abord un plan.")
    else:
        pid=st.selectbox("Plan",plans["id"],format_func=lambda x:f"{plans.loc[plans.id==x,'name'].iloc[0]} — {plans.loc[plans.id==x,'goal_name'].iloc[0]}",key="anplan")
        prow=plans[plans.id==pid].iloc[0]
        plan=json.loads(prow["plan_json"])
        weeks=[w["week"] for b in plan for w in b["weeks"]]
        week=st.selectbox("Semaine du plan",weeks)
        weekobj=next(w for b in plan for w in b["weeks"] if w["week"]==week)

        names=[f"{i+1}. {s['role']} — {s['title']}" for i,s in enumerate(weekobj["sessions"])]
        si=st.selectbox("Séance",range(len(names)),format_func=lambda i:names[i])
        ps=weekobj["sessions"][si]
        st.info(ps["description"])

        st.markdown("### Import de la séance réalisée")
        up=st.file_uploader("FIT, TCX, GPX ou CSV",type=["fit","tcx","gpx","csv"])

        summary={}
        lapdf=pd.DataFrame()
        intervals={}
        if up:
            try:
                records,laps,fmt=parse_activity(up)
                summary=summarize_activity(records)
                lapdf=detect_work_laps(analyze_laps(laps))
                intervals=interval_metrics(lapdf)

                st.success(f"{fmt} lu correctement.")
                c1,c2,c3,c4=st.columns(4)
                if summary.get("distance_km") is not None:c1.metric("Distance",f"{summary['distance_km']} km")
                if summary.get("pace_avg_sec") is not None:c2.metric("Allure",fmt_pace(summary["pace_avg_sec"]))
                if summary.get("hr_avg") is not None:c3.metric("FC moy",f"{summary['hr_avg']} bpm")
                if summary.get("decoupling_pct") is not None:c4.metric("Découplage",f"{summary['decoupling_pct']} %")

                if not lapdf.empty:
                    show=[c for c in ["Lap","Type","Durée (s)","Distance (m)","Allure","FC moy","FC max","Cadence","Puissance"] if c in lapdf.columns]
                    st.dataframe(lapdf[show],use_container_width=True)
            except Exception as e:
                st.error(f"Erreur de lecture : {e}")

        st.markdown("### RPE")
        rpe=st.slider("RPE de la séance /10",1,10,7)

        if st.button("Analyser et adapter le plan"):
            analysis,adjustment=analyze_session(ps,summary,intervals,rpe)
            st.markdown(analysis)

            newplan=adjust_plan(plan,week,adjustment)

            c0=db()
            c0.execute("UPDATE plans SET plan_json=? WHERE id=?",(json.dumps(newplan),int(pid)))
            c0.execute("""INSERT INTO sessions(athlete_id,plan_id,plan_week,session_date,sport,title,objective,planned_json,actual_json,rpe,analysis,adjustment_json,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (int(prow["athlete_id"]),int(pid),int(week),str(date.today()),"RUN/HYROX",ps["title"],ps["role"],json.dumps(ps),json.dumps({"summary":summary,"intervals":intervals}),int(rpe),analysis,json.dumps(adjustment),datetime.now().isoformat()))
            c0.commit();c0.close()

            st.success("Analyse sauvegardée et semaine suivante ajustée.")

with tabs[4]:
    c0=db()
    hist=pd.read_sql_query("""SELECT s.id,a.name,s.session_date,s.plan_week,s.title,s.objective,s.rpe,s.analysis
    FROM sessions s JOIN athletes a ON a.id=s.athlete_id ORDER BY s.id DESC""",c0)
    c0.close()
    if hist.empty:
        st.info("Aucune séance analysée.")
    else:
        st.dataframe(hist[["id","name","session_date","plan_week","title","objective","rpe"]],use_container_width=True)
        sid=st.selectbox("Ouvrir analyse",hist["id"])
        st.markdown(hist.loc[hist.id==sid,"analysis"].iloc[0])
