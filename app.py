
import streamlit as st
import sqlite3, json, io, math, xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, date
import pandas as pd
import numpy as np

DB_PATH=Path("coach_ai_run_v2.db")

def db():
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; return c

def init_db():
    c=db()
    c.execute("""CREATE TABLE IF NOT EXISTS athletes(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL, age INTEGER, sex TEXT, weight REAL, height REAL,
      hr_max INTEGER, hr_rest INTEGER, vma REAL, critical_speed REAL,
      goal TEXT, goal_date TEXT, weekly_km REAL, weekly_sessions INTEGER,
      pb_json TEXT, created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS sessions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      athlete_id INTEGER, session_date TEXT, title TEXT, objective TEXT,
      planned_json TEXT, actual_json TEXT, subjective_json TEXT,
      report TEXT, next_session TEXT, created_at TEXT
    )""")
    c.commit(); c.close()

def fmt_pace(sec):
    if sec is None or (isinstance(sec,float) and np.isnan(sec)): return "-"
    m=int(sec//60); s=int(round(sec%60))
    if s==60: m+=1; s=0
    return f"{m}:{s:02d}/km"

def pace_from_kmh(kmh):
    return 3600/kmh if kmh and kmh>0 else None

def sec_per_km_from_mps(mps):
    return 1000/mps if mps and mps>0 else None

def parse_fit(file):
    from fitparse import FitFile
    fit=FitFile(file)
    recs=[{f.name:f.value for f in r} for r in fit.get_messages("record")]
    laps=[{f.name:f.value for f in r} for r in fit.get_messages("lap")]
    return pd.DataFrame(recs),pd.DataFrame(laps)

def ends(el,suffix): return el.tag.lower().endswith(suffix.lower())

def parse_gpx(file):
    root=ET.parse(file).getroot(); rows=[]
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
    return pd.DataFrame(rows),pd.DataFrame()

def parse_tcx(file):
    root=ET.parse(file).getroot(); rows=[]
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
    return pd.DataFrame(rows),pd.DataFrame()

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
    return df.rename(columns=mp),pd.DataFrame()

def parse_activity(upload):
    ext=upload.name.lower().split(".")[-1]; upload.seek(0)
    if ext=="fit":
        r,l=parse_fit(upload); return r,l,"FIT"
    if ext=="tcx":
        r,l=parse_tcx(upload); return r,l,"TCX"
    if ext=="gpx":
        r,l=parse_gpx(upload); return r,l,"GPX"
    if ext=="csv":
        r,l=parse_csv(upload); return r,l,"CSV"
    raise ValueError("Format non pris en charge")

def summarize_run(df):
    out={}
    if df is None or df.empty: return out
    d=df.copy()
    if "timestamp" in d:
        d["timestamp"]=pd.to_datetime(d["timestamp"],errors="coerce")
        t=d["timestamp"].dropna()
        if len(t)>1: out["duration_min"]=round((t.max()-t.min()).total_seconds()/60,1)
    if "distance" in d:
        x=pd.to_numeric(d["distance"],errors="coerce").dropna()
        if len(x):
            mx=float(x.max()); out["distance_km"]=round(mx/1000 if mx>100 else mx,2)
    if "speed" in d:
        x=pd.to_numeric(d["speed"],errors="coerce").dropna(); x=x[x>0.5]
        if len(x):
            mps=float(x.mean()) if x.mean()<15 else float(x.mean()/3.6)
            out["speed_avg_kmh"]=round(mps*3.6,2)
            out["pace_avg_sec"]=round(1000/mps,1)
    elif out.get("distance_km") and out.get("duration_min"):
        kmh=out["distance_km"]/(out["duration_min"]/60)
        out["speed_avg_kmh"]=round(kmh,2); out["pace_avg_sec"]=round(3600/kmh,1)
    if "heart_rate" in d:
        x=pd.to_numeric(d["heart_rate"],errors="coerce").dropna()
        if len(x): out["hr_avg"]=round(float(x.mean()),1); out["hr_max"]=int(x.max())
    if "cadence" in d:
        x=pd.to_numeric(d["cadence"],errors="coerce").dropna()
        if len(x):
            avg=float(x.mean()); out["cadence_avg"]=round(avg*2 if avg<120 else avg,1)
    if "altitude" in d:
        x=pd.to_numeric(d["altitude"],errors="coerce").dropna()
        if len(x)>1:
            g=np.diff(x); out["elev_gain"]=round(float(g[g>0].sum()),0)
    if "heart_rate" in d and "speed" in d:
        tmp=d[["heart_rate","speed"]].copy()
        tmp["heart_rate"]=pd.to_numeric(tmp["heart_rate"],errors="coerce")
        tmp["speed"]=pd.to_numeric(tmp["speed"],errors="coerce")
        tmp=tmp.dropna(); tmp=tmp[(tmp.heart_rate>80)&(tmp.speed>0.5)]
        if len(tmp)>40:
            m=len(tmp)//2; a=tmp.iloc[:m]; b=tmp.iloc[m:]
            r1=a.speed.mean()/a.heart_rate.mean(); r2=b.speed.mean()/b.heart_rate.mean()
            out["decoupling_pct"]=round((r1-r2)/r1*100,1) if r1 else None
    return out

def analyze_laps(laps):
    if laps is None or laps.empty: return pd.DataFrame()
    rows=[]
    for _,r in laps.iterrows():
        dur=r.get("total_elapsed_time",r.get("total_timer_time",None))
        dist=r.get("total_distance",None)
        speed=r.get("avg_speed",r.get("enhanced_avg_speed",None))
        try:
            if (speed is None or pd.isna(speed)) and dur and dist: speed=dist/dur
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

def detect_fast_laps(lapdf):
    if lapdf.empty: return lapdf
    p=pd.to_numeric(lapdf["Allure_sec"],errors="coerce")
    valid=p.dropna()
    x=lapdf.copy()
    if len(valid)<3:
        x["Type"]="Lap"; return x
    med=valid.median()
    x["Type"]=np.where(p<med*0.92,"Travail",np.where(p>med*1.08,"Récup","Intermédiaire"))
    return x

def interval_metrics(lapdf):
    if lapdf.empty or "Type" not in lapdf: return {}
    work=lapdf[lapdf["Type"]=="Travail"]
    if work.empty: return {}
    p=pd.to_numeric(work["Allure_sec"],errors="coerce").dropna()
    out={"n_work":len(work)}
    if len(p):
        out["work_pace_avg"]=float(p.mean())
        out["work_pace_best"]=float(p.min())
        out["work_pace_worst"]=float(p.max())
        out["pace_spread_pct"]=round((p.max()-p.min())/p.mean()*100,1)
        if len(p)>1: out["first_last_change_pct"]=round((p.iloc[-1]-p.iloc[0])/p.iloc[0]*100,1)
    hr=pd.to_numeric(work["FC moy"],errors="coerce").dropna()
    if len(hr)>1:
        out["work_hr_first"]=round(float(hr.iloc[0]),1)
        out["work_hr_last"]=round(float(hr.iloc[-1]),1)
        out["work_hr_rise"]=round(float(hr.iloc[-1]-hr.iloc[0]),1)
    return out

def parse_pace_text(txt):
    try:
        t=txt.lower().replace("/km","").strip()
        m,s=t.split(":")
        return int(m)*60+int(s)
    except: return None

def suggested_session(athlete,objective,summary,intervals,sub):
    vma=float(athlete.get("vma") or 0)
    cs=float(athlete.get("critical_speed") or 0)
    goal=(athlete.get("goal") or "").lower()
    rpe=sub.get("rpe",7)
    fatigue=max(sub.get("muscle",3),sub.get("general",3))
    if fatigue>=5 or rpe>=9:
        return "Récupération","40–50 min très facile. Pas de séance de qualité tant que la fatigue reste élevée."
    obj=(objective or "").lower()
    if "vo₂" in obj or "vma" in obj:
        if vma:
            p100=pace_from_kmh(vma); p105=pace_from_kmh(vma*1.05)
            if intervals.get("pace_spread_pct",99)<=3 and rpe<=8:
                return "Progression VO₂",f"5 × 4 min entre {fmt_pace(p100)} et {fmt_pace(p105)}, récup 2'30 trot."
            return "Consolidation VO₂",f"6 × 3 min autour de {fmt_pace(p100)}, récup 2 min trot."
    if "seuil" in obj or "threshold" in obj:
        if cs:
            p=pace_from_kmh(cs); return "Seuil / vitesse critique",f"3 × 10 min autour de {fmt_pace(p)}, récup 2 min trot."
        if vma:
            p=pace_from_kmh(vma*.88); return "Seuil",f"3 × 10 min autour de {fmt_pace(p)}, récup 2 min."
    if "semi" in goal and cs:
        p=pace_from_kmh(cs*.94); return "Spécifique semi",f"3 × 3 km autour de {fmt_pace(p)}, récup 2'30 trot."
    if "10" in goal and vma:
        p=pace_from_kmh(vma*.92); return "Spécifique 10 km",f"5 × 1200 m autour de {fmt_pace(p)}, récup 1'30 trot."
    if "hyrox" in goal and cs:
        p=pace_from_kmh(cs*.93); return "Run HYROX",f"6 × 1 km autour de {fmt_pace(p)}, récup 1 min."
    return "Endurance + technique","60 min facile + 6 × 15 s de lignes droites, récupération complète."

def build_report(athlete,planned,summary,intervals,sub):
    parts=[]
    verdict="Séance exploitable"
    if sub["rpe"]>=9: verdict="Séance très coûteuse"
    elif intervals.get("pace_spread_pct",99)<=3 and sub["rpe"]<=8: verdict="Séance bien maîtrisée"
    parts.append(f"## Verdict\n**{verdict}**")

    g=[]
    if summary.get("distance_km") is not None:g.append(f"Distance : **{summary['distance_km']} km**.")
    if summary.get("duration_min") is not None:g.append(f"Durée : **{summary['duration_min']} min**.")
    if summary.get("pace_avg_sec") is not None:g.append(f"Allure moyenne : **{fmt_pace(summary['pace_avg_sec'])}**.")
    if summary.get("hr_avg") is not None:g.append(f"FC moyenne : **{summary['hr_avg']} bpm**, max **{summary.get('hr_max','-')} bpm**.")
    if summary.get("cadence_avg") is not None:g.append(f"Cadence moyenne : **{summary['cadence_avg']} pas/min**.")
    if summary.get("elev_gain") is not None:g.append(f"D+ estimé : **{summary['elev_gain']} m**.")
    parts.append("## Exécution globale\n"+"\n".join("- "+x for x in g))

    i=[]
    if intervals.get("n_work"):
        i.append(f"**{intervals['n_work']} fractions de travail** détectées.")
        i.append(f"Allure moyenne des fractions : **{fmt_pace(intervals['work_pace_avg'])}**.")
        i.append(f"Dispersion des allures : **{intervals.get('pace_spread_pct','-')} %**.")
        i.append(f"Évolution 1re → dernière : **{intervals.get('first_last_change_pct','-')} %**.")
        if intervals.get("work_hr_rise") is not None:i.append(f"FC moyenne +**{intervals['work_hr_rise']} bpm** de la 1re à la dernière fraction.")
    else:i.append("Pas assez de laps exploitables pour isoler automatiquement les fractions.")
    parts.append("## Analyse des répétitions\n"+"\n".join("- "+x for x in i))

    phys=[]
    dec=summary.get("decoupling_pct")
    if dec is not None:
        if abs(dec)<5:phys.append(f"Découplage allure/FC **{dec} %** : réponse cardiovasculaire stable.")
        elif dec<10:phys.append(f"Découplage allure/FC **{dec} %** : dérive modérée.")
        else:phys.append(f"Découplage allure/FC **{dec} %** : dérive importante.")
    spread=intervals.get("pace_spread_pct")
    if spread is not None:
        if spread<=2:phys.append("Régularité excellente.")
        elif spread<=5:phys.append("Régularité correcte.")
        else:phys.append("Dispersion importante : pacing ou cible à revoir.")
    fl=intervals.get("first_last_change_pct")
    if fl is not None:
        if fl>3:phys.append("Dernière fraction nettement plus lente : fatigue périphérique ou départ trop rapide probable.")
        elif fl<-3:phys.append("Fin nettement plus rapide : départ probablement trop prudent.")
        else:phys.append("Vitesse bien conservée jusqu'à la dernière répétition.")
    if sub["muscle"]>=4 and sub["general"]<=2:phys.append("Le ressenti suggère une limitation surtout musculaire.")
    if sub["rpe"]<=6 and spread is not None and spread<=3:phys.append("Stimulus bien toléré : marge de progression probable.")
    if sub["rpe"]>=9:phys.append("Coût perceptif très élevé : prudence sur la prochaine charge.")
    parts.append("## Lecture physiologique\n"+("\n".join("- "+x for x in phys) if phys else "- Données insuffisantes."))

    target=[]
    low=planned.get("target_pace_low_sec"); high=planned.get("target_pace_high_sec"); avg=intervals.get("work_pace_avg")
    if avg and low and high:
        if low<=avg<=high:target.append("L'allure moyenne des fractions est dans la cible.")
        elif avg<low:target.append("Les fractions ont été plus rapides que prévu.")
        else:target.append("Les fractions ont été plus lentes que prévu.")
    if sub.get("comment"):target.append(f"Commentaire athlète : *{sub['comment']}*")
    if sub.get("pain"):target.append(f"⚠️ Gêne : **{sub.get('pain_zone','non précisée')}**. Pas de progression tant que ce point n'est pas clarifié.")
    if not target:target.append("Pas de signal supplémentaire majeur.")
    parts.append("## Ce que j'en retiens comme coach\n"+"\n".join("- "+x for x in target))

    nxt_title,nxt=suggested_session(athlete,planned.get("objective",""),summary,intervals,sub)
    parts.append(f"## Séance recommandée ensuite\n**{nxt_title}**\n\n{nxt}")
    return "\n\n".join(parts),f"{nxt_title} — {nxt}"

init_db()
st.set_page_config(page_title="Coach IA RUN V2",layout="wide")
st.title("Coach IA — RUN V2")
st.caption("Analyse approfondie course à pied · FIT / TCX / GPX / CSV")

tab1,tab2,tab3=st.tabs(["Athlètes","Analyse RUN","Historique"])

with tab1:
    with st.form("athlete"):
        st.subheader("Profil athlète")
        a,b,c=st.columns(3)
        name=a.text_input("Nom / prénom"); age=a.number_input("Âge",10,100,30); sex=a.selectbox("Sexe",["Non renseigné","Femme","Homme","Autre"])
        weight=b.number_input("Poids (kg)",30.,200.,70.,0.1); height=b.number_input("Taille (cm)",100.,230.,175.,1.); hrmax=b.number_input("FC max",100,230,190)
        hrrest=c.number_input("FC repos/min",25,120,50); vma=c.number_input("VMA (km/h)",0.,30.,0.,0.1); cs=c.number_input("Vitesse critique (km/h)",0.,30.,0.,0.1)
        goal=st.text_input("Objectif principal"); goal_date=st.date_input("Date objectif",date.today())
        weekly_km=st.number_input("Volume habituel (km/semaine)",0.,250.,40.,1.); weekly_sessions=st.number_input("Séances/semaine",0,20,5)
        x,y,z=st.columns(3)
        pb5=x.text_input("PB 5 km"); pb10=x.text_input("PB 10 km"); pbhm=y.text_input("PB semi"); pbm=y.text_input("PB marathon"); pbhyrox=z.text_input("PB HYROX")
        if st.form_submit_button("Enregistrer") and name:
            pbs={"5k":pb5,"10k":pb10,"semi":pbhm,"marathon":pbm,"hyrox":pbhyrox}
            c0=db()
            c0.execute("""INSERT INTO athletes(name,age,sex,weight,height,hr_max,hr_rest,vma,critical_speed,goal,goal_date,weekly_km,weekly_sessions,pb_json,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(name,age,sex,weight,height,hrmax,hrrest,vma,cs,goal,str(goal_date),weekly_km,weekly_sessions,json.dumps(pbs),datetime.now().isoformat()))
            c0.commit();c0.close();st.success("Athlète enregistré.")
    c0=db(); ats=pd.read_sql_query("SELECT id,name,goal,vma,critical_speed,hr_max,hr_rest,weekly_km FROM athletes ORDER BY id DESC",c0);c0.close()
    if not ats.empty: st.dataframe(ats,use_container_width=True)

with tab2:
    c0=db(); ats=pd.read_sql_query("SELECT * FROM athletes ORDER BY name",c0);c0.close()
    if ats.empty: st.info("Crée d'abord un athlète.")
    else:
        aid=st.selectbox("Athlète",ats["id"],format_func=lambda x:ats.loc[ats.id==x,"name"].iloc[0])
        athlete=ats[ats.id==aid].iloc[0].to_dict()
        st.subheader("1. Séance prévue")
        title=st.text_input("Titre","6 × 1000 m")
        objective=st.selectbox("Objectif",["Endurance","Seuil / Threshold","VO₂max / VMA","Spécifique 10 km","Spécifique semi","HYROX run","Autre"])
        notes=st.text_area("Consignes prévues","Ex : 6 × 1000 m à 3'30–3'35/km, récup 1'30 trot.")
        a,b=st.columns(2)
        target_fast=a.text_input("Allure cible rapide","3:30/km")
        target_slow=b.text_input("Allure cible lente","3:35/km")
        planned={"objective":objective,"notes":notes,"target_pace_low_sec":parse_pace_text(target_fast),"target_pace_high_sec":parse_pace_text(target_slow)}
        if planned["target_pace_low_sec"] and planned["target_pace_high_sec"] and planned["target_pace_low_sec"]>planned["target_pace_high_sec"]:
            planned["target_pace_low_sec"],planned["target_pace_high_sec"]=planned["target_pace_high_sec"],planned["target_pace_low_sec"]

        st.subheader("2. Fichier réalisé")
        up=st.file_uploader("FIT, TCX, GPX ou CSV",type=["fit","tcx","gpx","csv"])
        summary={}; lapdf=pd.DataFrame(); intervals={}
        if up:
            try:
                records,laps,fmt=parse_activity(up); summary=summarize_run(records); lapdf=detect_fast_laps(analyze_laps(laps)); intervals=interval_metrics(lapdf)
                st.success(f"{fmt} lu correctement.")
                k1,k2,k3,k4=st.columns(4)
                if summary.get("distance_km") is not None:k1.metric("Distance",f"{summary['distance_km']} km")
                if summary.get("pace_avg_sec") is not None:k2.metric("Allure moyenne",fmt_pace(summary["pace_avg_sec"]))
                if summary.get("hr_avg") is not None:k3.metric("FC moyenne",f"{summary['hr_avg']} bpm")
                if summary.get("decoupling_pct") is not None:k4.metric("Découplage",f"{summary['decoupling_pct']} %")
                if not lapdf.empty:
                    show=[c for c in ["Lap","Type","Durée (s)","Distance (m)","Allure","FC moy","FC max","Cadence","Puissance"] if c in lapdf.columns]
                    st.dataframe(lapdf[show],use_container_width=True)
            except Exception as e: st.error(f"Erreur de lecture : {e}")

        st.subheader("3. Ressenti")
        a,b,c,d=st.columns(4)
        rpe=a.slider("RPE",1,10,7); sensations=b.slider("Sensations",1,5,3); muscle=c.slider("Fatigue musculaire",1,5,3); general=d.slider("Fatigue générale",1,5,3)
        pain=st.checkbox("Douleur / gêne"); pain_zone=st.text_input("Zone / intensité") if pain else ""; comment=st.text_area("Commentaire libre")
        sub={"rpe":rpe,"sensations":sensations,"muscle":muscle,"general":general,"pain":pain,"pain_zone":pain_zone,"comment":comment}
        if st.button("Analyser comme un coach"):
            report,next_session=build_report(athlete,planned,summary,intervals,sub); st.markdown(report)
            c0=db()
            c0.execute("""INSERT INTO sessions(athlete_id,session_date,title,objective,planned_json,actual_json,subjective_json,report,next_session,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",(int(aid),str(date.today()),title,objective,json.dumps(planned),json.dumps({"summary":summary,"intervals":intervals}),json.dumps(sub),report,next_session,datetime.now().isoformat()))
            c0.commit();c0.close();st.success("Rapport sauvegardé.")

with tab3:
    c0=db(); hist=pd.read_sql_query("""SELECT s.id,a.name,s.session_date,s.title,s.objective,s.report FROM sessions s JOIN athletes a ON a.id=s.athlete_id ORDER BY s.id DESC""",c0);c0.close()
    if hist.empty: st.info("Pas encore de rapport.")
    else:
        st.dataframe(hist[["id","name","session_date","title","objective"]],use_container_width=True)
        sid=st.selectbox("Ouvrir un rapport",hist["id"])
        st.markdown(hist.loc[hist.id==sid,"report"].iloc[0])
