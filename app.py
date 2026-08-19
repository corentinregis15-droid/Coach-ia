
import streamlit as st
import sqlite3, json, io, xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, date
import pandas as pd
import numpy as np

DB_PATH = Path("coach_ai.db")

def get_conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c=get_conn()
    c.execute("""CREATE TABLE IF NOT EXISTS athletes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, age INTEGER, sex TEXT, height_cm REAL, weight_kg REAL,
        hr_max INTEGER, hr_rest INTEGER, vma REAL, critical_speed REAL, ftp REAL,
        goal TEXT, goal_date TEXT, weekly_hours REAL, weekly_sessions INTEGER,
        pb_json TEXT, strength_json TEXT, erg_json TEXT, created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS sessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        athlete_id INTEGER, session_date TEXT, sport TEXT, title TEXT, objective TEXT,
        planned_json TEXT, actual_json TEXT, subjective_json TEXT, report TEXT, created_at TEXT
    )""")
    c.commit(); c.close()

def hr_zones(hr_max, hr_rest):
    if not hr_max: return {}
    cuts=[(.50,.60),(.60,.70),(.70,.80),(.80,.90),(.90,1.0)]
    out={}
    for i,(a,b) in enumerate(cuts,1):
        if hr_rest:
            reserve=hr_max-hr_rest
            lo=round(hr_rest+a*reserve); hi=round(hr_rest+b*reserve)
        else:
            lo=round(a*hr_max); hi=round(b*hr_max)
        out[f"Z{i}"]=(lo,hi)
    return out

def parse_fit(file):
    from fitparse import FitFile
    fit=FitFile(file)
    rows=[]
    for rec in fit.get_messages("record"):
        d={f.name:f.value for f in rec}
        if d: rows.append(d)
    return pd.DataFrame(rows)

def _tag_ends(el, suffix):
    return el.tag.lower().endswith(suffix.lower())

def parse_gpx(file):
    root=ET.parse(file).getroot()
    rows=[]
    for pt in root.iter():
        if _tag_ends(pt, "trkpt"):
            row={}
            lat=pt.attrib.get("lat"); lon=pt.attrib.get("lon")
            if lat: row["latitude"]=float(lat)
            if lon: row["longitude"]=float(lon)
            for ch in pt.iter():
                tag=ch.tag.lower()
                text=(ch.text or "").strip()
                if not text: continue
                if tag.endswith("time"): row["timestamp"]=text
                elif tag.endswith("ele"): 
                    try: row["altitude"]=float(text)
                    except: pass
                elif tag.endswith("hr"):
                    try: row["heart_rate"]=float(text)
                    except: pass
                elif tag.endswith("cad"):
                    try: row["cadence"]=float(text)
                    except: pass
                elif tag.endswith("power") or tag.endswith("watts"):
                    try: row["power"]=float(text)
                    except: pass
            rows.append(row)
    return pd.DataFrame(rows)

def parse_tcx(file):
    root=ET.parse(file).getroot()
    rows=[]
    for tp in root.iter():
        if _tag_ends(tp, "Trackpoint"):
            row={}
            for ch in tp.iter():
                tag=ch.tag.lower()
                text=(ch.text or "").strip()
                if not text: continue
                if tag.endswith("time"): row["timestamp"]=text
                elif tag.endswith("distancemeters"):
                    try: row["distance"]=float(text)
                    except: pass
                elif tag.endswith("altitudemeters"):
                    try: row["altitude"]=float(text)
                    except: pass
                elif tag.endswith("heartratebpm"):
                    # nested Value handled below
                    pass
                elif tag.endswith("value") and "heartrate" in str(ch.getparent()) if hasattr(ch, "getparent") else False:
                    pass
                elif tag.endswith("cadence"):
                    try: row["cadence"]=float(text)
                    except: pass
                elif tag.endswith("watts"):
                    try: row["power"]=float(text)
                    except: pass
                elif tag.endswith("speed"):
                    try: row["speed"]=float(text)
                    except: pass
            # robust HR search
            for hrnode in tp.iter():
                if _tag_ends(hrnode, "HeartRateBpm"):
                    for sub in hrnode.iter():
                        if _tag_ends(sub, "Value") and (sub.text or "").strip():
                            try: row["heart_rate"]=float(sub.text.strip())
                            except: pass
            rows.append(row)
    return pd.DataFrame(rows)

def parse_csv(file):
    raw=file.read()
    try:
        text=raw.decode("utf-8")
    except:
        text=raw.decode("latin1")
    df=pd.read_csv(io.StringIO(text))
    # normalize common names
    mapping={}
    for c in df.columns:
        lc=c.lower().strip()
        if lc in ["time","timestamp","datetime","date_time"]: mapping[c]="timestamp"
        elif lc in ["hr","heart rate","heart_rate","heartrate"]: mapping[c]="heart_rate"
        elif lc in ["power","watts","watt"]: mapping[c]="power"
        elif lc in ["cadence","rpm"]: mapping[c]="cadence"
        elif lc in ["distance","distance_m","meters"]: mapping[c]="distance"
        elif lc in ["speed","speed_m_s","velocity"]: mapping[c]="speed"
        elif lc in ["altitude","elevation"]: mapping[c]="altitude"
    return df.rename(columns=mapping)

def parse_activity(upload):
    ext=upload.name.lower().split(".")[-1]
    upload.seek(0)
    if ext=="fit": return parse_fit(upload), "FIT"
    if ext=="gpx": return parse_gpx(upload), "GPX"
    if ext=="tcx": return parse_tcx(upload), "TCX"
    if ext=="csv": return parse_csv(upload), "CSV"
    raise ValueError("Format non pris en charge.")

def summarize(df):
    out={}
    if df is None or df.empty: return out
    if "timestamp" in df.columns:
        t=pd.to_datetime(df["timestamp"], errors="coerce").dropna()
        if len(t)>1: out["duration_min"]=round((t.max()-t.min()).total_seconds()/60,1)
    if "distance" in df.columns:
        d=pd.to_numeric(df["distance"],errors="coerce").dropna()
        if len(d):
            mx=float(d.max())
            out["distance_km"]=round(mx/1000 if mx>100 else mx,2)
    if "heart_rate" in df.columns:
        x=pd.to_numeric(df["heart_rate"],errors="coerce").dropna()
        if len(x):
            out["hr_avg"]=round(float(x.mean()),1)
            out["hr_max"]=round(float(x.max()),1)
            m=len(x)//2
            if m>10:
                a=x.iloc[:m].mean(); b=x.iloc[m:].mean()
                out["hr_drift_pct"]=round(float((b-a)/a*100),1) if a else None
    if "power" in df.columns:
        x=pd.to_numeric(df["power"],errors="coerce").dropna()
        if len(x):
            out["power_avg"]=round(float(x.mean()),1)
            out["power_max"]=round(float(x.max()),1)
            out["power_cv_pct"]=round(float(x.std()/x.mean()*100),1) if x.mean() else None
    if "cadence" in df.columns:
        x=pd.to_numeric(df["cadence"],errors="coerce").dropna()
        if len(x): out["cadence_avg"]=round(float(x.mean()),1)
    if "speed" in df.columns:
        x=pd.to_numeric(df["speed"],errors="coerce").dropna()
        if len(x):
            # if likely m/s, convert to km/h
            avg=float(x.mean())
            out["speed_avg_kmh"]=round(avg*3.6 if avg<20 else avg,2)
    return out

def build_report(planned, actual, sub):
    sections=[]
    sections.append("### Verdict\n**Séance analysée**")
    sections.append("### Objectif\n"+(planned.get("objective") or "Non renseigné"))

    labels=[
        ("duration_min","Durée","min"),("distance_km","Distance","km"),
        ("hr_avg","FC moyenne","bpm"),("hr_max","FC max","bpm"),
        ("power_avg","Puissance moyenne","W"),("power_max","Puissance max","W"),
        ("cadence_avg","Cadence moyenne","rpm"),("speed_avg_kmh","Vitesse moyenne","km/h"),
        ("hr_drift_pct","Dérive cardiaque","%"),("power_cv_pct","Variabilité puissance","%")
    ]
    bullets=[]
    for k,l,u in labels:
        if actual.get(k) is not None:
            bullets.append(f"- {l} : **{actual[k]} {u}**")
    sections.append("### Données objectives\n"+("\n".join(bullets) if bullets else "- Données insuffisantes."))

    sections.append(
        "### Ressenti\n"
        f"- RPE : **{sub['rpe']}/10**\n"
        f"- Sensations : **{sub['sensations']}/5**\n"
        f"- Fatigue musculaire : **{sub['muscle']}/5**\n"
        f"- Fatigue générale : **{sub['general']}/5**\n"
        + (f"- Commentaire : {sub['comment']}\n" if sub.get("comment") else "")
        + (f"- Douleur/gêne : {sub['pain_zone']}\n" if sub.get("pain") else "")
    )

    interp=[]
    d=actual.get("hr_drift_pct")
    if d is not None:
        if d<5: interp.append("Dérive cardiaque faible : bon contrôle aérobie.")
        elif d<10: interp.append("Dérive cardiaque modérée : surveiller pacing, chaleur, hydratation et fatigue.")
        else: interp.append("Dérive cardiaque élevée : le coût interne augmente nettement au fil de la séance.")
    cv=actual.get("power_cv_pct")
    if cv is not None:
        if cv<5: interp.append("Puissance très régulière.")
        elif cv<10: interp.append("Puissance globalement maîtrisée.")
        else: interp.append("Puissance très variable : vérifier terrain, relances ou capacité à tenir la cible.")
    if sub["muscle"]>=4 and sub["general"]<=2:
        interp.append("Le signal de fatigue semble surtout musculaire/périphérique.")
    if sub["rpe"]>=9:
        interp.append("Coût perceptif très élevé : ne pas augmenter volume et intensité simultanément.")
    elif sub["rpe"]<=6:
        interp.append("Coût perceptif contenu : une marge de progression peut exister selon l'objectif.")

    sections.append("### Interprétation coach\n"+("\n".join("- "+x for x in interp) if interp else "- À compléter avec davantage de données."))

    decision="Maintenir la progression et comparer avec la prochaine séance similaire."
    if sub["rpe"]>=9:
        decision="Ne pas augmenter la difficulté immédiatement. Répéter ou alléger selon la récupération."
    elif sub["rpe"]<=6 and d is not None and d<5:
        decision="Progression modérée possible : augmenter soit le volume, soit l'intensité, pas les deux."
    sections.append("### Décision proposée\n"+decision)
    return "\n\n".join(sections)

init_db()
st.set_page_config(page_title="Coach IA Cloud V2", layout="wide")
st.title("Coach IA — Cloud V2")
st.caption("Compatible iPad · FIT / TCX / GPX / CSV")

t1,t2,t3=st.tabs(["Athlètes","Analyser une séance","Historique"])

with t1:
    st.subheader("Fiche athlète")
    with st.form("ath"):
        a,b,c=st.columns(3)
        name=a.text_input("Nom / prénom"); age=a.number_input("Âge",10,100,30); sex=a.selectbox("Sexe",["Non renseigné","Femme","Homme","Autre"])
        height=b.number_input("Taille (cm)",100.,230.,175.); weight=b.number_input("Poids (kg)",30.,200.,70.)
        hrmax=b.number_input("FC max",100,230,190); hrrest=c.number_input("FC repos/min",25,120,50)
        vma=c.number_input("VMA (km/h)",0.,30.,0.,0.1); cs=c.number_input("Vitesse critique (km/h)",0.,30.,0.,0.1); ftp=c.number_input("FTP vélo (W)",0.,600.,0.,1.)
        goal=st.text_input("Objectif principal"); goal_date=st.date_input("Date objectif",date.today())
        wh=st.number_input("Volume habituel h/semaine",0.,40.,5.,0.5); ws=st.number_input("Séances/semaine",0,20,5)

        st.markdown("#### Records")
        x,y,z=st.columns(3)
        pb5=x.text_input("PB 5 km"); pb10=x.text_input("PB 10 km"); pbhm=y.text_input("PB semi"); pbm=y.text_input("PB marathon"); pbh=z.text_input("PB HYROX"); cat=z.text_input("Catégorie HYROX")

        st.markdown("#### Force")
        x,y,z=st.columns(3)
        squat=x.number_input("Squat (kg)",0.,400.,0.); dead=x.number_input("Deadlift (kg)",0.,400.,0.)
        fs=y.number_input("Front squat (kg)",0.,300.,0.); bench=y.number_input("Bench (kg)",0.,300.,0.)
        pp=z.number_input("Push press (kg)",0.,300.,0.); pull=z.number_input("Tractions lestées +kg",0.,150.,0.)

        st.markdown("#### Ergomètres")
        x,y=st.columns(2); ski=x.text_input("SkiErg 2 km"); row=y.text_input("RowErg 2 km")

        if st.form_submit_button("Enregistrer") and name:
            pbs={"5k":pb5,"10k":pb10,"semi":pbhm,"marathon":pbm,"hyrox":pbh,"category":cat}
            strength={"squat":squat,"deadlift":dead,"front_squat":fs,"bench":bench,"push_press":pp,"weighted_pullup":pull}
            erg={"ski2k":ski,"row2k":row}
            c0=get_conn()
            c0.execute("""INSERT INTO athletes(name,age,sex,height_cm,weight_kg,hr_max,hr_rest,vma,critical_speed,ftp,goal,goal_date,weekly_hours,weekly_sessions,pb_json,strength_json,erg_json,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name,age,sex,height,weight,hrmax,hrrest,vma,cs,ftp,goal,str(goal_date),wh,ws,json.dumps(pbs),json.dumps(strength),json.dumps(erg),datetime.now().isoformat()))
            c0.commit(); c0.close(); st.success("Athlète enregistré.")

    c0=get_conn(); aths=pd.read_sql_query("SELECT id,name,goal,goal_date,hr_max,hr_rest,vma,critical_speed,ftp FROM athletes ORDER BY id DESC",c0); c0.close()
    if not aths.empty:
        st.dataframe(aths,use_container_width=True)

with t2:
    c0=get_conn(); aths=pd.read_sql_query("SELECT * FROM athletes ORDER BY name",c0); c0.close()
    if aths.empty:
        st.info("Crée d'abord un athlète.")
    else:
        aid=st.selectbox("Athlète",aths["id"],format_func=lambda i:aths.loc[aths.id==i,"name"].iloc[0])
        sport=st.selectbox("Sport",["Run","Vélo","HYROX","Musculation","SkiErg","RowErg","Autre"])
        title=st.text_input("Titre","5 x 4 min")
        objective=st.text_area("Objectif","Développer la VO₂max / puissance aérobie.")
        notes=st.text_area("Structure / consignes","Ex : 5 x 4 min, récup 2'30.")

        st.markdown("#### Fichier réalisé")
        upload=st.file_uploader("Importer FIT, TCX, GPX ou CSV",type=["fit","tcx","gpx","csv"])
        actual={}
        if upload:
            try:
                df,fmt=parse_activity(upload)
                actual=summarize(df)
                st.success(f"Fichier {fmt} lu correctement.")
                st.json(actual)
                cols=[c for c in ["timestamp","heart_rate","power","cadence","distance","speed","altitude"] if c in df.columns]
                if cols:
                    st.dataframe(df[cols].head(500),use_container_width=True)
            except Exception as e:
                st.error(f"Impossible de lire ce fichier : {e}")

        st.markdown("#### Ressenti")
        a,b,c,d=st.columns(4)
        rpe=a.slider("RPE",1,10,7); sens=b.slider("Sensations",1,5,3); muscle=c.slider("Fatigue musculaire",1,5,3); general=d.slider("Fatigue générale",1,5,3)
        pain=st.checkbox("Douleur / gêne"); pain_zone=st.text_input("Zone / intensité") if pain else ""; comment=st.text_area("Commentaire libre")

        if st.button("Générer le rapport coach"):
            planned={"objective":objective,"notes":notes}
            sub={"rpe":rpe,"sensations":sens,"muscle":muscle,"general":general,"pain":pain,"pain_zone":pain_zone,"comment":comment}
            report=build_report(planned,actual,sub)
            st.markdown(report)
            c0=get_conn()
            c0.execute("""INSERT INTO sessions(athlete_id,session_date,sport,title,objective,planned_json,actual_json,subjective_json,report,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",(int(aid),str(date.today()),sport,title,objective,json.dumps(planned),json.dumps(actual),json.dumps(sub),report,datetime.now().isoformat()))
            c0.commit(); c0.close()
            st.success("Rapport sauvegardé.")

with t3:
    c0=get_conn()
    hist=pd.read_sql_query("""SELECT s.id,a.name,s.session_date,s.sport,s.title,s.objective,s.report FROM sessions s JOIN athletes a ON a.id=s.athlete_id ORDER BY s.id DESC""",c0)
    c0.close()
    if hist.empty: st.info("Aucune séance.")
    else:
        st.dataframe(hist[["id","name","session_date","sport","title","objective"]],use_container_width=True)
        sid=st.selectbox("Ouvrir un rapport",hist["id"])
        st.markdown(hist.loc[hist.id==sid,"report"].iloc[0])
