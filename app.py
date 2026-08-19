
import streamlit as st
import sqlite3, json, math, io, xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np

APP_VERSION="V12"
DB=Path("coach_ia_v12.db")

# ========================= STYLE =========================
st.set_page_config(page_title=f"Coach IA Pro {APP_VERSION}",layout="wide",page_icon="⚡")
st.markdown("""
<style>
.block-container{max-width:1550px;padding-top:1rem;padding-bottom:4rem}
[data-testid="stAppViewContainer"]{
background:
radial-gradient(circle at 10% 0%,rgba(239,68,68,.10),transparent 26%),
radial-gradient(circle at 90% 0%,rgba(59,130,246,.08),transparent 25%),
linear-gradient(180deg,#0d1016,#121722);color:#f5f7fb}
.hero{padding:26px;border:1px solid #2d364a;border-radius:24px;
background:linear-gradient(135deg,#222b3b,#11151e);margin-bottom:16px}
.hero h1{margin:0;font-size:2.45rem}.hero p{margin:6px 0 0;color:#aeb9cc}
.week{background:#151b26;border:1px solid #2d374b;border-radius:18px;padding:16px;margin:14px 0}
.session{background:#10151e;border:1px solid #273146;border-radius:14px;padding:13px;margin:9px 0}
.exercise{background:#0e141d;border:1px solid #263147;border-radius:12px;padding:10px 12px;margin:7px 0}
.tip{background:#12251d;border-left:4px solid #4ade80;padding:11px 13px;border-radius:10px;margin:8px 0}
.info{background:#142238;border-left:4px solid #60a5fa;padding:11px 13px;border-radius:10px;margin:8px 0}
.warn{background:#2b2114;border-left:4px solid #f59e0b;padding:11px 13px;border-radius:10px;margin:8px 0}
.raceA{background:#31171a;border-left:4px solid #ef4444;padding:11px 13px;border-radius:10px;margin:8px 0}
.raceB{background:#2b2414;border-left:4px solid #f59e0b;padding:11px 13px;border-radius:10px;margin:8px 0}
.raceC{background:#142238;border-left:4px solid #60a5fa;padding:11px 13px;border-radius:10px;margin:8px 0}
.pill{display:inline-block;background:#252e40;border-radius:999px;padding:5px 9px;margin:3px;font-size:12px;color:#dfe7f4}
.small{font-size:13px;color:#9eabc0}
</style>
""",unsafe_allow_html=True)

# ========================= DB =========================
def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
    c=db()
    c.execute("""CREATE TABLE IF NOT EXISTS athletes(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL, age INTEGER, sex TEXT,
      hr_max INTEGER, hr_rest INTEGER, vma REAL, critical_speed REAL,
      weekly_sessions INTEGER, weekly_km REAL, training_years REAL,
      run_strength_enabled INTEGER, run_strength_sessions INTEGER,
      weaknesses_json TEXT, created_at TEXT, updated_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS competitions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      athlete_id INTEGER, race_type TEXT, name TEXT, race_date TEXT,
      priority TEXT, notes TEXT, created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS plans(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      athlete_id INTEGER, primary_competition_id INTEGER,
      created_at TEXT, plan_json TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS sessions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      athlete_id INTEGER, plan_id INTEGER, plan_week INTEGER,
      session_date TEXT, title TEXT, objective TEXT,
      planned_json TEXT, actual_json TEXT, rpe INTEGER,
      analysis TEXT, created_at TEXT
    )""")
    c.commit(); c.close()
init_db()

# ========================= HELPERS =========================
def loads(v,d):
    try:return json.loads(v or "")
    except:return d

def athletes():
    c=db(); x=pd.read_sql_query("SELECT * FROM athletes ORDER BY name",c); c.close(); return x

def comps(aid=None):
    c=db()
    if aid is None:
        x=pd.read_sql_query("SELECT * FROM competitions ORDER BY race_date",c)
    else:
        x=pd.read_sql_query("SELECT * FROM competitions WHERE athlete_id=? ORDER BY race_date",c,params=[aid])
    c.close(); return x

def latest_plan(aid):
    c=db();x=pd.read_sql_query("SELECT * FROM plans WHERE athlete_id=? ORDER BY id DESC LIMIT 1",c,params=[aid]);c.close()
    return None if x.empty else x.iloc[0].to_dict()

def fmt_date(d):
    names=["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"]
    return f"{names[d.weekday()]} {d.strftime('%d/%m')}"

def monday_of(d):
    return d - timedelta(days=d.weekday())

def fmt_pace(sec):
    if sec is None:return "-"
    m=int(sec//60);s=int(round(sec%60))
    if s==60:m+=1;s=0
    return f"{m}:{s:02d}/km"

def pace_from_pct_vma(vma,pct):
    if not vma:return None
    return 3600/(vma*pct/100)

def target_vma(a,lo,hi):
    v=float(a.get("vma") or 0)
    if not v:return f"{lo}-{hi}% VMA"
    return f"{lo}-{hi}% VMA · {fmt_pace(pace_from_pct_vma(v,lo))} à {fmt_pace(pace_from_pct_vma(v,hi))}"

def target_hr(a,lo,hi):
    m=int(a.get("hr_max") or 0)
    if not m:return f"{lo}-{hi}% FCmax"
    return f"{round(m*lo/100)}-{round(m*hi/100)} bpm ({lo}-{hi}% FCmax)"

def tier(a):
    v=float(a.get("vma") or 0); km=float(a.get("weekly_km") or 0); yrs=float(a.get("training_years") or 0)
    s=(2 if v>=17 else 1 if v>=14 else 0)+(2 if km>=60 else 1 if km>=30 else 0)+(2 if yrs>=4 else 1 if yrs>=1 else 0)
    return "Avancé" if s>=5 else "Intermédiaire" if s>=3 else "Débutant / reprise"

# ========================= BLOCKS =========================
def block_structure(total_weeks, race_type):
    # blocs 4-6 semaines, taper à part
    if total_weeks<=7:
        return [("Spécifique compétition",max(4,total_weeks-1)),("Affûtage",1)]
    blocks=[]
    remain=total_weeks
    if race_type=="HYROX":
        candidates=["Base aérobie & technique","Force","Développement","Spécifique compétition"]
    else:
        candidates=["Base aérobie","Force & économie","Développement","Spécifique compétition"]

    idx=0
    while remain>8:
        dur=5 if remain>=13 else 4
        blocks.append((candidates[min(idx,len(candidates)-1)],dur))
        remain-=dur; idx+=1
    if remain>=6:
        blocks.append(("Spécifique compétition",remain-2))
        blocks.append(("Affûtage",2))
    else:
        blocks.append(("Spécifique compétition",max(4,remain-1)))
        blocks.append(("Affûtage",1))
    return blocks

# ========================= RUN SESSIONS =========================
def run_threshold(a,i):
    t=tier(a)
    seq={
      "Avancé":[("3×8 min",88,90,"2 min"),("3×10 min",88,91,"2 min"),("4×8 min",89,91,"90 s"),("2×15 min",88,90,"3 min"),("3×12 min",89,91,"2 min")],
      "Intermédiaire":[("3×8 min",86,89,"2 min"),("3×9 min",87,90,"2 min"),("3×10 min",87,90,"2 min"),("4×7 min",88,90,"90 s"),("2×15 min",87,90,"3 min")],
      "Débutant / reprise":[("3×6 min",84,87,"2 min"),("3×7 min",84,88,"2 min"),("3×8 min",85,88,"2 min"),("4×6 min",85,88,"2 min"),("2×12 min",85,88,"3 min")]
    }[t]
    m,lo,hi,r=seq[min(i,len(seq)-1)]
    return {"title":"Seuil","objective":"Développer le seuil et la stabilité d'allure.","warmup":"15-20 min facile + mobilité + 4×20 s progressifs.","main":m,"vma":target_vma(a,lo,hi),"hr":target_hr(a,85,92),"recovery":r+" trot facile.","cooldown":"10-15 min facile.","tip":"Première répétition volontairement contrôlée."}

def run_vo2(a,i):
    t=tier(a)
    seq={
      "Avancé":[("6×3 min",98,100,"2 min"),("5×4 min",97,100,"2'30"),("4×5 min",95,98,"3 min"),("2×8×30/30",105,110,"3 min entre séries"),("12×1 min",105,110,"1 min")],
      "Intermédiaire":[("8×2 min",95,100,"2 min"),("6×3 min",95,100,"2 min"),("5×3 min",98,100,"2 min"),("10×400 m",100,105,"1 min"),("2×6×30/30",105,110,"3 min entre séries")],
      "Débutant / reprise":[("8×300 m",95,100,"1'15"),("2×5×300 m",98,100,"1 min / 3 min"),("6×2 min",95,100,"2 min"),("8×1 min",100,105,"1'30"),("10×200 m",100,105,"1 min")]
    }[t]
    m,lo,hi,r=seq[min(i,len(seq)-1)]
    return {"title":"VO₂max","objective":"Développer la puissance aérobie sans casser la mécanique.","warmup":"20 min facile + éducatifs + 4×20 s progressifs.","main":m,"vma":target_vma(a,lo,hi),"hr":target_hr(a,90,97),"recovery":r,"cooldown":"12-15 min facile.","tip":"La régularité compte plus que la FC sur les premières fractions."}

def run_easy(a,d=50):
    return {"title":"Endurance fondamentale","objective":"Construire le moteur à faible coût.","warmup":"10 min très progressifs.","main":f"{d} min continu.","vma":target_vma(a,60,72),"hr":target_hr(a,65,78),"recovery":"Aucune.","cooldown":"5 min très souples.","tip":"Rester facile même si les jambes sont bonnes."}

def run_long(a,i,specific=False):
    base=75 if tier(a)=="Débutant / reprise" else 90 if tier(a)=="Intermédiaire" else 105
    d=base+min(i*10,30)
    main=f"{d} min en endurance."
    if specific: main+=f" Ajouter 2×{15+min(i*5,15)} min à 80-88% VMA, récup 5 min."
    return {"title":"Sortie longue spécifique" if specific else "Sortie longue","objective":"Développer résistance et économie sous fatigue.","warmup":"10-15 min très faciles.","main":main,"vma":target_vma(a,65,78),"hr":target_hr(a,68,82),"recovery":"5 min facile entre blocs spécifiques." if specific else "Aucune.","cooldown":"5-10 min faciles.","tip":"Finir avec encore un peu de marge."}

def run_specific(a,race_type,race_name,i):
    g=race_name.lower()
    if race_type=="HYROX":
        # important: PAS de compromised run ici; il est traité séparément et uniquement dans bloc spécifique
        seq=["6×1 km","7×1 km","8×1 km","5×1200 m","4×1600 m"]
        lo,hi=84,90; hr=(85,93); rec="60-90 s trot"
    elif "semi" in g:
        seq=["3×2 km","3×3 km","2×4 km","3×4 km","2×5 km"];lo,hi=82,88;hr=(85,92);rec="2'30-4 min trot"
    elif "10" in g:
        seq=["6×1 km","5×1200 m","4×1600 m","4×2 km","3×2 km + 4×400 m"];lo,hi=88,94;hr=(88,94);rec="1'30-2 min trot"
    else:
        seq=["6×800 m","6×1 km","5×1200 m","4×1600 m","3×2 km"];lo,hi=90,96;hr=(90,95);rec="1'30-2 min trot"
    return {"title":"Spécifique course","objective":"Se rapprocher progressivement des contraintes de course.","warmup":"20 min facile + éducatifs + 4×20 s progressifs.","main":seq[min(i,len(seq)-1)],"vma":target_vma(a,lo,hi),"hr":target_hr(a,*hr),"recovery":rec,"cooldown":"12-15 min facile.","tip":"Spécifique ne veut pas dire maximal."}

# ========================= STRENGTH =========================
def strength_upper(block):
    if "Force" in block:
        return [("Bench press","5×4","80-85%","3 min"),("Tractions lestées","5×4-5","RPE 8","2'30"),("Row poitrine appuyée","4×6","RPE 8","2 min"),("Push press","4×3","75-80%","2'30"),("Farmer hold","4×30 s","Lourd","60-90 s")]
    if "Développement" in block:
        return [("Push press","5×3","75-80%","2'30"),("Bench press","3×5","75-80%","2 min"),("Tractions lestées","4×5","RPE 8","2 min"),("Wall Balls technique","8×10","RPE 5-6","45 s"),("Suitcase carry","3×30m/côté","Lourd","60-90 s")]
    return [("Bench press","4×5","75-80%","2'30"),("Tractions lestées","4×6","RPE 7-8","2 min"),("Row poitrine appuyée","4×8","RPE 8","90 s"),("Push press","4×4","70-75%","2 min")]

def strength_lower(block):
    if "Force" in block:
        return [("Back squat","5×3","82-88%","3-4 min"),("Romanian deadlift","4×5","80%","3 min"),("Bulgarian split squat","3×6/jambe","RPE 8","2 min"),("Leg press","3×8","RPE 8","2 min")]
    if "Développement" in block:
        return [("Back squat","4×3","85-90%","3-4 min"),("Box jump","4×3","Max qualité","2 min"),("Front squat","3×5","RPE 7","2 min"),("Sled Push lourd","5×15m","Lourd","3 min"),("Sled Pull lourd","5×15m","Lourd","3 min")]
    return [("Back squat","4×4","75-82%","3 min"),("Bulgarian split squat","3×6/jambe","RPE 8","2 min"),("Romanian deadlift","3×6","RPE 7-8","2 min"),("Mollets","3×12","Contrôlé","60 s")]

def force_endurance_complexes(block):
    # proposés surtout hors bloc spécifique, travail station sans compromised run
    if "Base" in block or "Force" in block:
        return [
            ("Complexe Sled Push","4 tours","10 Front Squats → immédiatement 10-15 m Sled Push","Récup 2 min"),
            ("Complexe Sled Pull","4 tours","8 Romanian Deadlifts → immédiatement 10-15 m Sled Pull","Récup 2 min"),
            ("Complexe Wall Balls","4 tours","8 Push Press → 15 Wall Balls","Récup 90 s")
        ]
    return [
        ("Complexe Sled Push","4-5 tours","8 Front Squats → 15 m Sled Push","Récup 2-2'30"),
        ("Complexe Sled Pull","4-5 tours","8 Row lourds → 15 m Sled Pull","Récup 2 min"),
        ("Complexe Carry","4 tours","20 m Farmer Carry lourd → 30 s hold","Récup 90 s")
    ]

def compromised_specific(i):
    seq=[
        "4 tours : 1 km run + 1 station HYROX ciblée · récup 2 min",
        "5 tours : 1 km run + station · récup 90 s",
        "6 tours : 1 km run + station · récup 60-90 s",
        "4 blocs course : 1 km + station à charge course, transitions rapides"
    ]
    return seq[min(i,len(seq)-1)]

# ========================= CALENDAR / DAYS =========================
def day_templates(goal_type,n,strength_sessions=0):
    if goal_type=="RUN":
        # preserve spacing between run quality sessions
        base=["Mardi","Jeudi","Dimanche"]
        if n==4: base=["Mardi","Jeudi","Samedi","Dimanche"]
        elif n==5: base=["Mardi","Mercredi","Vendredi","Samedi","Dimanche"]
        elif n>=6: base=["Lundi","Mardi","Mercredi","Vendredi","Samedi","Dimanche"]+["Jeudi"]*(n-6)
        return base[:n]
    else:
        base=["Lundi","Mardi","Jeudi","Samedi","Dimanche"]
        if n<=3:base=["Mardi","Jeudi","Dimanche"]
        elif n==4:base=["Lundi","Mardi","Jeudi","Dimanche"]
        elif n>=6:base=["Lundi","Mardi","Mercredi","Jeudi","Samedi","Dimanche"]+["Vendredi"]*(n-6)
        return base[:n]

DAY_IDX={"Lundi":0,"Mardi":1,"Mercredi":2,"Jeudi":3,"Vendredi":4,"Samedi":5,"Dimanche":6}

def week_dates(start_monday,week_num,day_names):
    monday=start_monday+timedelta(weeks=week_num-1)
    return [monday+timedelta(days=DAY_IDX[d]) for d in day_names]

# ========================= BUILD PLAN =========================
def build_plan(a,primary,all_races):
    start=monday_of(date.today())
    race_date=pd.to_datetime(primary["race_date"]).date()
    total=max(1,math.ceil((race_date-start).days/7))
    blocks=block_structure(total,primary["race_type"])
    n=int(a.get("weekly_sessions") or 4)
    run_strength=bool(a.get("run_strength_enabled"))
    strength_n=int(a.get("run_strength_sessions") or 0) if run_strength and primary["race_type"]=="RUN" else 0

    plan=[];week=1
    for block_name,dur in blocks:
        B={"name":block_name,"duration":dur,"weeks":[]}
        for i in range(dur):
            assimilation=(i==dur-1 and block_name!="Affûtage")
            sessions=[]

            if primary["race_type"]=="RUN":
                # core running sessions
                if "Base" in block_name:
                    sessions.append({"type":"RUN","content":run_threshold(a,i)})
                elif "Force" in block_name:
                    sessions.append({"type":"RUN","content":run_threshold(a,max(0,i-1))})
                elif "Développement" in block_name:
                    sessions.append({"type":"RUN","content":run_vo2(a,i)})
                elif "Spécifique" in block_name:
                    sessions.append({"type":"RUN","content":run_specific(a,"RUN",primary["name"],i)})
                else:
                    sessions.append({"type":"RUN","content":run_easy(a,35)})

                if n>=3:sessions.append({"type":"RUN","content":run_easy(a,40 if assimilation else 50)})
                if n>=4:
                    q2=run_vo2(a,max(0,i-1)) if "Base" in block_name else run_threshold(a,i)
                    sessions.append({"type":"RUN","content":q2})
                if n>=2:sessions.append({"type":"RUN","content":run_long(a,i,"Spécifique" in block_name)})

                # optional strength for runners
                for sidx in range(strength_n):
                    if sidx%2==0:
                        sessions.insert(1,{"type":"MUSCU","title":"Renforcement lower","details":strength_lower(block_name)})
                    else:
                        sessions.insert(2,{"type":"MUSCU","title":"Renforcement upper","details":strength_upper(block_name)})

            else:
                # HYROX
                # No compromised run until final specific block
                if "Base" in block_name:
                    sessions.append({"type":"RUN","content":run_threshold(a,i)})
                    sessions.append({"type":"MUSCU","title":"Lower force","details":strength_lower(block_name)})
                    sessions.append({"type":"HYROX_FE","title":"Endurance de force","details":force_endurance_complexes(block_name)})
                    if n>=4:sessions.append({"type":"RUN","content":run_easy(a,50)})
                    if n>=5:sessions.append({"type":"MUSCU","title":"Upper + stations","details":strength_upper(block_name)})
                elif "Force" in block_name:
                    sessions.append({"type":"MUSCU","title":"Lower force maximale","details":strength_lower(block_name)})
                    sessions.append({"type":"RUN","content":run_threshold(a,max(0,i-1))})
                    sessions.append({"type":"MUSCU","title":"Upper force","details":strength_upper(block_name)})
                    sessions.append({"type":"HYROX_FE","title":"Endurance de force","details":force_endurance_complexes(block_name)})
                    if n>=5:sessions.append({"type":"RUN","content":run_easy(a,50)})
                elif "Développement" in block_name:
                    sessions.append({"type":"RUN","content":run_vo2(a,i)})
                    sessions.append({"type":"MUSCU","title":"Lower puissance + stations","details":strength_lower(block_name)})
                    sessions.append({"type":"RUN","content":run_easy(a,55)})
                    sessions.append({"type":"HYROX_FE","title":"Endurance de force","details":force_endurance_complexes(block_name)})
                    if n>=5:sessions.append({"type":"MUSCU","title":"Upper + stations","details":strength_upper(block_name)})
                else:
                    # Spécifique only now: introduce compromised run
                    sessions.append({"type":"RUN","content":run_specific(a,"HYROX",primary["name"],i)})
                    sessions.append({"type":"MUSCU","title":"Force entretien","details":strength_lower("Développement")[:3]})
                    sessions.append({"type":"RUN","content":run_easy(a,45)})
                    sessions.append({"type":"HYROX_COMP","title":"Compromised run spécifique","details":compromised_specific(i)})
                    if n>=5:sessions.append({"type":"MUSCU","title":"Upper + stations","details":strength_upper("Développement")})

            # trim only if generated over requested count
            if len(sessions)>n+strength_n:
                sessions=sessions[:n+strength_n]

            # assign days/dates
            day_names=day_templates(primary["race_type"],len(sessions),strength_n)
            dates=week_dates(start,week,day_names)
            for idx,s in enumerate(sessions):
                s["day_name"]=day_names[idx]
                s["date"]=str(dates[idx])

            # secondary races in this week
            week_start=start+timedelta(weeks=week-1)
            week_end=week_start+timedelta(days=6)
            races=[]
            for _,r in all_races.iterrows():
                rd=pd.to_datetime(r["race_date"]).date()
                if week_start<=rd<=week_end:
                    races.append({"name":r["name"],"type":r["race_type"],"date":str(rd),"priority":r["priority"]})

            if races:
                # If B/C race, reduce surrounding training. If A race is primary, taper already applies.
                for r in races:
                    if r["priority"] in ["B","C"] and r["id"] if "id" in r else False:
                        pass

            if assimilation:
                for s in sessions:
                    s["assimilation"]="Semaine d'assimilation : volume -25 à -35 %, conserver un peu d'intensité et beaucoup de qualité technique."

            B["weeks"].append({
                "week":week,
                "start":str(week_start),
                "end":str(week_end),
                "assimilation":assimilation,
                "races":races,
                "sessions":sessions
            })
            week+=1
        plan.append(B)
    return plan

# ========================= RENDER =========================
def render_run(c):
    st.markdown(f"### {c['title']}")
    st.write(f"**Objectif :** {c['objective']}")
    st.write(f"**Échauffement :** {c['warmup']}")
    st.write(f"**Corps de séance :** {c['main']}")
    st.write(f"**Cible VMA / allure :** {c['vma']}")
    st.write(f"**Cible cardio :** {c['hr']}")
    st.write(f"**Récupération :** {c['recovery']}")
    st.write(f"**Retour au calme :** {c['cooldown']}")
    st.markdown(f"<div class='tip'>💡 {c['tip']}</div>",unsafe_allow_html=True)

def render_strength(title,details):
    st.markdown(f"### {title}")
    for row in details:
        if len(row)==4:
            a,b,c,d=row
            st.markdown(f"<div class='exercise'><b>{a}</b><br><span class='small'>{b} · {c} · {d}</span></div>",unsafe_allow_html=True)

# ========================= UI =========================
st.markdown(f"""<div class="hero"><h1>⚡ Coach IA Pro {APP_VERSION}</h1>
<p>Calendrier daté · multi-compétitions · blocs 4-6 semaines · force · endurance de force · spécifique HYROX tardif</p></div>""",unsafe_allow_html=True)

tabs=st.tabs(["👤 Athlètes","🏁 Compétitions","🧭 Générer plan","📅 Voir plan"])

# -------- ATHLETES --------
with tabs[0]:
    A=athletes();mode=st.radio("Action",["Créer","Modifier"],horizontal=True)
    row=None
    if mode=="Modifier" and not A.empty:
        eid=st.selectbox("Athlète",A.id,format_func=lambda x:A.loc[A.id==x,"name"].iloc[0])
        row=A[A.id==eid].iloc[0].to_dict()
    D=lambda k,v:row.get(k,v) if row else v

    with st.form("ath_form"):
        c1,c2,c3=st.columns(3)
        name=c1.text_input("Nom",D("name",""))
        age=c1.number_input("Âge",10,100,int(D("age",30) or 30))
        sex=c1.selectbox("Sexe",["Non renseigné","Femme","Homme","Autre"])
        hrmax=c2.number_input("FC max",100,230,int(D("hr_max",190) or 190))
        hrrest=c2.number_input("FC repos",25,120,int(D("hr_rest",50) or 50))
        vma=c2.number_input("VMA",0.,30.,float(D("vma",0) or 0),.1)
        cs=c3.number_input("Vitesse critique",0.,30.,float(D("critical_speed",0) or 0),.1)
        ws=c3.number_input("Séances RUN/HYROX par semaine",1,12,int(D("weekly_sessions",4) or 4))
        km=c3.number_input("Km run / semaine",0.,250.,float(D("weekly_km",30) or 30))
        yrs=c3.number_input("Années d'entraînement",0.,30.,float(D("training_years",1) or 1),.5)

        strength_enabled=st.checkbox("Si objectif RUN : souhaite faire du renforcement musculaire",value=bool(D("run_strength_enabled",0)))
        strength_sessions=st.number_input("Nombre de séances de renforcement / semaine",0,4,int(D("run_strength_sessions",0) or 0),disabled=not strength_enabled)

        weakness_opts=["Endurance","Seuil","VO₂max","Vitesse","Économie","Sortie longue","Sled Push","Sled Pull","Wall Balls","Running compromis","SkiErg","RowErg","Force","Endurance de force"]
        weaknesses=st.multiselect("Points faibles / priorités",weakness_opts,default=[x for x in loads(D("weaknesses_json","[]"),[]) if x in weakness_opts])

        if st.form_submit_button("Enregistrer") and name:
            c=db()
            vals=(name,age,sex,hrmax,hrrest,vma,cs,ws,km,yrs,int(strength_enabled),strength_sessions,json.dumps(weaknesses),datetime.now().isoformat())
            if row:
                c.execute("""UPDATE athletes SET name=?,age=?,sex=?,hr_max=?,hr_rest=?,vma=?,critical_speed=?,weekly_sessions=?,weekly_km=?,training_years=?,run_strength_enabled=?,run_strength_sessions=?,weaknesses_json=?,updated_at=? WHERE id=?""",vals+(int(row["id"]),))
            else:
                c.execute("""INSERT INTO athletes(name,age,sex,hr_max,hr_rest,vma,critical_speed,weekly_sessions,weekly_km,training_years,run_strength_enabled,run_strength_sessions,weaknesses_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          vals+(datetime.now().isoformat(),))
            c.commit();c.close();st.success("Athlète enregistré.")

    A=athletes()
    if not A.empty:
        st.dataframe(A[["id","name","vma","hr_max","weekly_sessions","weekly_km","run_strength_enabled","run_strength_sessions"]],use_container_width=True)

# -------- COMPETITIONS --------
with tabs[1]:
    A=athletes()
    if A.empty:
        st.info("Crée d'abord un athlète.")
    else:
        aid=st.selectbox("Athlète",A.id,format_func=lambda x:A.loc[A.id==x,"name"].iloc[0],key="raceath")
        with st.form("race_form"):
            c1,c2,c3=st.columns(3)
            rt=c1.selectbox("Type",["RUN","HYROX"])
            rn=c1.text_input("Nom de la compétition")
            rd=c2.date_input("Date",date.today()+timedelta(days=60))
            pr=c2.selectbox("Priorité",["A","B","C"],help="A = objectif principal, B = importante, C = préparation/test.")
            notes=c3.text_area("Notes")
            if st.form_submit_button("Ajouter la compétition") and rn:
                c=db();c.execute("""INSERT INTO competitions(athlete_id,race_type,name,race_date,priority,notes,created_at) VALUES(?,?,?,?,?,?,?)""",
                                 (int(aid),rt,rn,str(rd),pr,notes,datetime.now().isoformat()));c.commit();c.close();st.success("Compétition ajoutée.")
        C=comps(int(aid))
        if not C.empty:
            st.dataframe(C[["id","race_type","name","race_date","priority","notes"]],use_container_width=True)

# -------- GENERATE --------
with tabs[2]:
    A=athletes()
    if A.empty:st.info("Crée un athlète.")
    else:
        aid=st.selectbox("Athlète",A.id,format_func=lambda x:A.loc[A.id==x,"name"].iloc[0],key="planath")
        a=A[A.id==aid].iloc[0].to_dict()
        C=comps(int(aid))
        if C.empty:
            st.info("Ajoute au moins une compétition.")
        else:
            cid=st.selectbox("Compétition principale pour ce plan",C.id,format_func=lambda x:f"{C.loc[C.id==x,'name'].iloc[0]} · {C.loc[C.id==x,'race_date'].iloc[0]} · priorité {C.loc[C.id==x,'priority'].iloc[0]}")
            primary=C[C.id==cid].iloc[0].to_dict()
            st.write(f"**Niveau estimé : {tier(a)}**")
            st.write(f"**Objectif principal : {primary['name']} — {primary['race_date']}**")
            st.write(f"Autres compétitions intégrées au calendrier : **{max(0,len(C)-1)}**")
            if st.button("Générer le plan daté"):
                plan=build_plan(a,primary,C)
                c=db();c.execute("""INSERT INTO plans(athlete_id,primary_competition_id,created_at,plan_json) VALUES(?,?,?,?)""",
                                 (int(aid),int(cid),datetime.now().isoformat(),json.dumps(plan)));c.commit();c.close();st.success("Plan généré.")

# -------- VIEW --------
with tabs[3]:
    A=athletes()
    if not A.empty:
        aid=st.selectbox("Athlète",A.id,format_func=lambda x:A.loc[A.id==x,"name"].iloc[0],key="view")
        lp=latest_plan(int(aid))
        if not lp:
            st.info("Pas encore de plan.")
        else:
            plan=loads(lp["plan_json"],[])
            for b in plan:
                st.markdown(f"## Bloc : {b['name']} — {b['duration']} semaines")
                for w in b["weeks"]:
                    wstart=pd.to_datetime(w["start"]).date();wend=pd.to_datetime(w["end"]).date()
                    label=f"Semaine {w['week']} · {wstart.strftime('%d/%m')} → {wend.strftime('%d/%m')}"
                    if w["assimilation"]:label+=" · ASSIMILATION"
                    with st.expander(label,expanded=w["week"]<=2):
                        for race in w["races"]:
                            css="raceA" if race["priority"]=="A" else "raceB" if race["priority"]=="B" else "raceC"
                            st.markdown(f"<div class='{css}'>🏁 {race['priority']} · {race['name']} · {pd.to_datetime(race['date']).strftime('%d/%m/%Y')}</div>",unsafe_allow_html=True)

                        for s in w["sessions"]:
                            d=pd.to_datetime(s["date"]).date()
                            st.markdown(f"### {fmt_date(d)} · {s['day_name']}")
                            if s["type"]=="RUN":
                                render_run(s["content"])
                            elif s["type"]=="MUSCU":
                                render_strength(s["title"],s["details"])
                            elif s["type"]=="HYROX_FE":
                                st.markdown(f"### {s['title']}")
                                for name,sets,combo,rest in s["details"]:
                                    st.markdown(f"<div class='exercise'><b>{name}</b><br><span class='small'>{sets} · {combo} · {rest}</span></div>",unsafe_allow_html=True)
                            else:
                                st.markdown(f"### {s['title']}")
                                st.write(s["details"])
                            if s.get("assimilation"):
                                st.markdown(f"<div class='warn'>{s['assimilation']}</div>",unsafe_allow_html=True)
