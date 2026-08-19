
import streamlit as st
import sqlite3, json, math
from datetime import date, datetime
from pathlib import Path
import pandas as pd
import numpy as np

APP_VERSION="V10"
DB=Path("coach_ia_v10.db")

st.set_page_config(page_title=f"Coach IA Pro {APP_VERSION}", layout="wide", page_icon="⚡")

st.markdown("""
<style>
.block-container{max-width:1500px;padding-top:1rem;padding-bottom:4rem}
[data-testid="stAppViewContainer"]{
background:linear-gradient(180deg,#0d1016,#121722);
color:#f5f7fb}
.hero{padding:24px;border:1px solid #2d364a;border-radius:22px;
background:linear-gradient(135deg,#202838,#11151e);margin-bottom:16px}
.hero h1{margin:0;font-size:2.4rem}
.hero p{margin:6px 0 0;color:#aeb9cc}
.card{background:#171d28;border:1px solid #2b3549;border-radius:16px;padding:16px;margin:9px 0}
.session{background:#141a24;border:1px solid #2a3448;border-radius:16px;padding:16px;margin:10px 0}
.tip{background:#12251d;border-left:4px solid #4ade80;padding:11px 13px;border-radius:10px;margin:8px 0}
.info{background:#142238;border-left:4px solid #60a5fa;padding:11px 13px;border-radius:10px;margin:8px 0}
.warn{background:#2b2114;border-left:4px solid #f59e0b;padding:11px 13px;border-radius:10px;margin:8px 0}
.pill{display:inline-block;background:#252e40;border-radius:999px;padding:5px 9px;margin:3px;font-size:12px;color:#dfe7f4}
.exercise{background:#10151e;border:1px solid #273146;border-radius:12px;padding:10px 12px;margin:7px 0}
.small{font-size:13px;color:#9eabc0}
</style>
""",unsafe_allow_html=True)

def db():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

def init_db():
    c=db()
    c.execute("""CREATE TABLE IF NOT EXISTS athletes(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL, age INTEGER, sex TEXT,
      hr_max INTEGER, hr_rest INTEGER, vma REAL, critical_speed REAL,
      weekly_sessions INTEGER, weekly_km REAL, training_years REAL,
      goal_type TEXT, goal_name TEXT, goal_date TEXT,
      weaknesses_json TEXT, created_at TEXT, updated_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS plans(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      athlete_id INTEGER, created_at TEXT, plan_json TEXT
    )""")
    c.commit();c.close()

init_db()

def loads(v,d):
    try:return json.loads(v or "")
    except:return d

def athletes():
    c=db();df=pd.read_sql_query("SELECT * FROM athletes ORDER BY name",c);c.close();return df

def latest_plan(aid):
    c=db();df=pd.read_sql_query("SELECT * FROM plans WHERE athlete_id=? ORDER BY id DESC LIMIT 1",c,params=[aid]);c.close()
    return None if df.empty else df.iloc[0].to_dict()

def weeks_to_goal(d):
    return max(1,math.ceil((d-date.today()).days/7))

def fmt_pace(sec):
    if sec is None:return "-"
    m=int(sec//60);s=int(round(sec%60))
    if s==60:m+=1;s=0
    return f"{m}:{s:02d}/km"

def pace_from_pct_vma(vma,pct):
    if not vma or pct<=0:return None
    return 3600/(vma*(pct/100))

def hr_zone_text(a,lo,hi):
    hrmax=int(a.get("hr_max") or 0)
    if not hrmax:return f"{lo}-{hi}% FCmax"
    return f"{round(hrmax*lo/100)}-{round(hrmax*hi/100)} bpm ({lo}-{hi}% FCmax)"

def pace_target(a,lo,hi):
    v=float(a.get("vma") or 0)
    if not v:return f"{lo}-{hi}% VMA"
    slow=fmt_pace(pace_from_pct_vma(v,lo))
    fast=fmt_pace(pace_from_pct_vma(v,hi))
    return f"{lo}-{hi}% VMA · {slow} à {fast}"

def athlete_tier(a):
    v=float(a.get("vma") or 0)
    km=float(a.get("weekly_km") or 0)
    yrs=float(a.get("training_years") or 0)
    score=0
    score += 2 if v>=17 else 1 if v>=14 else 0
    score += 2 if km>=60 else 1 if km>=30 else 0
    score += 2 if yrs>=4 else 1 if yrs>=1 else 0
    if score>=5:return "Avancé"
    if score>=3:return "Intermédiaire"
    return "Débutant / reprise"

# -------- blocs 4-6 semaines --------
def make_blocks(total_weeks):
    """
    Tous les blocs font 4 à 6 semaines, sauf affûtage final 1-2 semaines.
    Chaque bloc long contient une semaine d'assimilation.
    """
    if total_weeks <= 6:
        return [("Spécifique", max(4,total_weeks-1)), ("Affûtage",1)]
    blocks=[]
    remaining=total_weeks
    while remaining>8:
        dur=5 if remaining>=13 else 4
        name="Base" if len(blocks)==0 else "Développement" if len(blocks)==1 else "Spécifique"
        blocks.append((name,dur))
        remaining-=dur
    if remaining>=5:
        blocks.append(("Spécifique",remaining-2))
        blocks.append(("Affûtage",2))
    else:
        blocks.append(("Spécifique",max(4,remaining-1)))
        blocks.append(("Affûtage",1))
    return blocks

# -------- séances RUN précises --------
def threshold_session(a,week_idx,tier):
    seq_adv=[
        ("3 × 8 min",88,90,2),
        ("3 × 10 min",88,91,2),
        ("4 × 8 min",89,91,1.5),
        ("2 × 15 min",88,90,3),
        ("3 × 12 min",89,91,2),
    ]
    seq_int=[
        ("3 × 8 min",86,89,2),
        ("3 × 9 min",87,90,2),
        ("3 × 10 min",87,90,2),
        ("4 × 7 min",88,90,1.5),
        ("2 × 15 min",87,90,3),
    ]
    seq_beg=[
        ("3 × 6 min",84,87,2),
        ("3 × 7 min",84,88,2),
        ("3 × 8 min",85,88,2),
        ("4 × 6 min",85,88,2),
        ("2 × 12 min",85,88,3),
    ]
    seq=seq_adv if tier=="Avancé" else seq_int if tier=="Intermédiaire" else seq_beg
    name,lo,hi,rec=seq[min(week_idx,len(seq)-1)]
    return {
        "title":"Seuil / Threshold",
        "objective":"Développer la capacité à maintenir une forte fraction de la VMA sans dérive excessive.",
        "warmup":"15–20 min facile + mobilité dynamique + 4 × 20 s progressifs / 40 s facile.",
        "main":name,
        "vma":pace_target(a,lo,hi),
        "hr":hr_zone_text(a,85,92),
        "recovery":f"{rec} min en trot très facile entre les répétitions.",
        "cooldown":"10–15 min très facile.",
        "tip":"La première répétition doit paraître presque trop facile. Chercher une allure stable jusqu'à la dernière."
    }

def vo2_session(a,week_idx,tier):
    if tier=="Avancé":
        seq=[
            ("6 × 3 min",98,100,"2 min"),
            ("5 × 4 min",97,100,"2'30"),
            ("4 × 5 min",95,98,"3 min"),
            ("2 × 8 × 30/30",105,110,"3 min entre séries"),
            ("12 × 1 min",105,110,"1 min")
        ]
    elif tier=="Intermédiaire":
        seq=[
            ("8 × 2 min",95,100,"2 min"),
            ("6 × 3 min",95,100,"2 min"),
            ("5 × 3 min",98,100,"2 min"),
            ("10 × 400 m",100,105,"1 min"),
            ("2 × 6 × 30/30",105,110,"3 min entre séries")
        ]
    else:
        seq=[
            ("8 × 300 m",95,100,"1'15"),
            ("2 × 5 × 300 m",98,100,"1 min / 3 min entre séries"),
            ("6 × 2 min",95,100,"2 min"),
            ("8 × 1 min",100,105,"1 min 30"),
            ("10 × 200 m",100,105,"1 min")
        ]
    main,lo,hi,rec=seq[min(week_idx,len(seq)-1)]
    return {
        "title":"VO₂max / VMA",
        "objective":"Stimuler la puissance aérobie tout en conservant une bonne mécanique de course.",
        "warmup":"20 min facile + 4 × 20 s progressifs + 2 × 30 s proche allure séance.",
        "main":main,
        "vma":pace_target(a,lo,hi),
        "hr":hr_zone_text(a,90,97),
        "recovery":rec,
        "cooldown":"12–15 min très facile.",
        "tip":"Ne juge pas la séance uniquement à la FC : elle monte avec retard. La régularité des fractions reste prioritaire."
    }

def easy_session(a,duration=50):
    return {
        "title":"Endurance fondamentale",
        "objective":"Développer le volume aérobie avec un coût mécanique et métabolique faible.",
        "warmup":"Pas d'échauffement séparé : partir très doucement 10 min.",
        "main":f"{duration} min en continu.",
        "vma":pace_target(a,60,72),
        "hr":hr_zone_text(a,65,78),
        "recovery":"Aucune.",
        "cooldown":"5 min très souples si besoin.",
        "tip":"Si tu dois surveiller l'allure pour ne pas accélérer, c'est probablement que la séance est bien placée."
    }

def long_session(a,week_idx,tier,specific=False):
    base=75 if tier=="Débutant / reprise" else 90 if tier=="Intermédiaire" else 105
    dur=base+min(week_idx*10,30)
    main=f"{dur} min en endurance."
    if specific:
        main+=f" Inclure 2 × {15+min(week_idx*5,15)} min à 80–88% VMA, récup 5 min facile."
    return {
        "title":"Sortie longue" + (" spécifique" if specific else ""),
        "objective":"Développer la résistance à la fatigue et la capacité à maintenir l'économie de course.",
        "warmup":"10–15 min très faciles.",
        "main":main,
        "vma":pace_target(a,65,78) + (" sur la partie facile." if specific else ""),
        "hr":hr_zone_text(a,68,82),
        "recovery":"5 min facile entre les blocs spécifiques." if specific else "Aucune.",
        "cooldown":"5–10 min très faciles.",
        "tip":"Finir encore capable d'accélérer légèrement. La sortie longue n'est pas un test."
    }

def specific_session(a,goal,week_idx,tier):
    g=goal.lower()
    if "semi" in g:
        reps=["3 × 2 km","3 × 3 km","2 × 4 km","3 × 4 km","2 × 5 km"]
        main=reps[min(week_idx,len(reps)-1)]
        lo,hi=82,88
        rec="2'30 à 4 min trot selon la longueur."
        hr=(85,92)
    elif "10" in g:
        reps=["6 × 1 km","5 × 1200 m","4 × 1600 m","4 × 2 km","3 × 2 km + 4 × 400 m"]
        main=reps[min(week_idx,len(reps)-1)]
        lo,hi=88,94
        rec="1'30 à 2 min trot."
        hr=(88,94)
    else:
        reps=["6 × 800 m","6 × 1 km","5 × 1200 m","4 × 1600 m","3 × 2 km"]
        main=reps[min(week_idx,len(reps)-1)]
        lo,hi=90,96
        rec="1'30 à 2 min trot."
        hr=(90,95)
    return {
        "title":"Spécifique course",
        "objective":"Rapprocher progressivement la séance des contraintes de la compétition.",
        "warmup":"20 min facile + éducatifs + 4 × 20 s progressifs.",
        "main":main,
        "vma":pace_target(a,lo,hi),
        "hr":hr_zone_text(a,*hr),
        "recovery":rec,
        "cooldown":"12–15 min facile.",
        "tip":"La séance spécifique doit ressembler à la course sans devenir une course."
    }

# -------- muscu détaillée --------
def strength_session(block,upper=True):
    if upper:
        if block=="Base":
            return [
                ("Bench press","4 × 5","75–80% 1RM","2'30"),
                ("Tractions lestées","4 × 6","RPE 7–8","2'"),
                ("Row poitrine appuyée","4 × 8","RPE 8","90 s"),
                ("Push press","4 × 4","70–75%","2'"),
                ("Suitcase carry","3 × 30 m/côté","Lourd","60–90 s")
            ]
        if block=="Développement":
            return [
                ("Push press","5 × 3","75–80%","2'30"),
                ("Bench press","3 × 5","75–80%","2'"),
                ("Tractions lestées","5 × 5","RPE 8","2'"),
                ("Row poitrine appuyée","4 × 6","RPE 8","2'"),
                ("Wall Balls technique","8 × 10","RPE 5–6","45 s")
            ]
        return [
            ("Push press","4 × 3","Explosif","2'"),
            ("Bench press","3 × 4","RPE 7","2'"),
            ("Row poitrine appuyée","3 × 8","RPE 7","90 s"),
            ("Wall Balls","6 × 15","Cadence course","45–60 s"),
            ("Farmer hold","3 × 30 s","Lourd","60 s")
        ]
    else:
        if block=="Base":
            return [
                ("Back squat","5 × 3","82–87% 1RM","3–4'"),
                ("Bulgarian split squat","3 × 6/jambe","RPE 8","2'"),
                ("Romanian deadlift","3 × 6","RPE 7–8","2'"),
                ("Leg press","3 × 8","RPE 8","2'"),
                ("Mollets debout","3 × 12","Contrôlé","60 s")
            ]
        if block=="Développement":
            return [
                ("Back squat","4 × 3","85–90%","3–4'"),
                ("Box jump","4 × 3","Max qualité","2'"),
                ("Front squat","3 × 5","RPE 7","2'"),
                ("Sled Push lourd","6 × 15 m","Lourd, pas max","3'"),
                ("Sled Pull lourd","6 × 15 m","Lourd, pas max","3'")
            ]
        return [
            ("Back squat","3 × 2","85%","3'"),
            ("Box jump","3 × 3","Explosif","2'"),
            ("Sled Push charge course","5 × 15 m","Vitesse","2'30"),
            ("Sled Pull charge course","5 × 15 m","Vitesse","2'30"),
            ("Front squat","3 × 4","RPE 7","2'")
        ]

def build_plan(a):
    total=weeks_to_goal(pd.to_datetime(a["goal_date"]).date())
    blocks=make_blocks(total)
    tier=athlete_tier(a)
    n=int(a.get("weekly_sessions") or 4)
    gt=a["goal_type"]
    goal=a["goal_name"]
    plan=[]
    week_counter=1

    for block_name,dur in blocks:
        block={"name":block_name,"duration":dur,"weeks":[]}
        for i in range(dur):
            assimilation=(i==dur-1 and block_name!="Affûtage")
            sessions=[]

            if gt=="RUN":
                # quality 1
                if block_name=="Base":
                    q1=threshold_session(a,i,tier)
                elif block_name=="Développement":
                    q1=vo2_session(a,i,tier)
                elif block_name=="Spécifique":
                    q1=specific_session(a,goal,i,tier)
                else:
                    q1=vo2_session(a,0,tier)
                    q1["main"]="4 × 1 min vive / 2 min facile"
                    q1["vma"]=pace_target(a,95,100)
                    q1["hr"]=hr_zone_text(a,85,92)

                sessions.append({"day":"Séance 1","type":"RUN","content":q1})

                if n>=3:
                    sessions.append({"day":"Séance 2","type":"RUN","content":easy_session(a,45 if assimilation else 50)})
                if n>=4:
                    q2=threshold_session(a,i,tier) if block_name!="Base" else vo2_session(a,max(0,i-1),tier)
                    sessions.append({"day":"Séance 3","type":"RUN","content":q2})
                if n>=2:
                    sessions.append({"day":"Week-end","type":"RUN","content":long_session(a,i,tier,block_name=="Spécifique")})

                while len(sessions)<n:
                    sessions.insert(-1,{"day":"À placer","type":"RUN","content":easy_session(a,40 if assimilation else 50)})

            else:
                # HYROX: run + upper + lower + conditioning
                runq=threshold_session(a,i,tier) if block_name=="Base" else vo2_session(a,i,tier) if block_name=="Développement" else specific_session(a,"hyrox",i,tier)
                sessions.append({"day":"Jour 1","type":"RUN","content":runq})
                if n>=2:sessions.append({"day":"Jour 2","type":"MUSCU","title":"Upper + stations","details":strength_session(block_name,True)})
                if n>=3:sessions.append({"day":"Jour 3","type":"RUN","content":easy_session(a,45 if assimilation else 55)})
                if n>=4:sessions.append({"day":"Jour 4","type":"MUSCU","title":"Lower + stations","details":strength_session(block_name,False)})
                if n>=5:
                    sessions.append({"day":"Jour 5","type":"HYROX","title":"Conditioning HYROX",
                                     "details":"4–6 tours : 1 km run + 1 station prioritaire. "
                                               + ("RPE 5–6, technique et transitions." if block_name=="Base"
                                                  else "RPE 7, qualité constante." if block_name=="Développement"
                                                  else "RPE 7–8, charge proche course et transitions rapides.")})

            if assimilation:
                for s in sessions:
                    s["assimilation"]="Semaine d'assimilation : réduire le volume total de 25–35 %, conserver un peu d'intensité et beaucoup de qualité technique."

            if block_name=="Affûtage":
                for s in sessions:
                    s["assimilation"]="Affûtage : volume -40 à -60 %, maintien de rappels courts à allure course, aucune séance destructrice."

            block["weeks"].append({"week":week_counter,"assimilation":assimilation,"sessions":sessions})
            week_counter+=1

        plan.append(block)
    return plan

def render_run(c):
    st.markdown(f"### {c['title']}")
    st.write(f"**Objectif :** {c['objective']}")
    st.write(f"**Échauffement :** {c['warmup']}")
    st.write(f"**Corps de séance :** {c['main']}")
    st.write(f"**Cible VMA/allure :** {c['vma']}")
    st.write(f"**Cible cardio :** {c['hr']}")
    st.write(f"**Récupération :** {c['recovery']}")
    st.write(f"**Retour au calme :** {c['cooldown']}")
    st.markdown(f"<div class='tip'>💡 {c['tip']}</div>",unsafe_allow_html=True)

def render_strength(title,details):
    st.markdown(f"### {title}")
    for name,sets,intensity,rest in details:
        st.markdown(f"<div class='exercise'><b>{name}</b><br><span class='small'>Séries/répétitions : {sets} · Intensité : {intensity} · Repos : {rest}</span></div>",unsafe_allow_html=True)

st.markdown(f"""<div class="hero"><h1>⚡ Coach IA Pro {APP_VERSION}</h1>
<p>Blocs 4–6 semaines · semaines d'assimilation · séances RUN détaillées · HYROX & musculation</p></div>""",unsafe_allow_html=True)

tabs=st.tabs(["👤 Athlètes","🧭 Générer plan","📅 Voir plan"])

with tabs[0]:
    A=athletes()
    mode=st.radio("Action",["Créer","Modifier"],horizontal=True)
    row=None
    if mode=="Modifier" and not A.empty:
        eid=st.selectbox("Athlète",A.id,format_func=lambda x:A.loc[A.id==x,"name"].iloc[0])
        row=A[A.id==eid].iloc[0].to_dict()
    D=lambda k,v:row.get(k,v) if row else v

    with st.form("ath"):
        c1,c2,c3=st.columns(3)
        name=c1.text_input("Nom",D("name",""))
        age=c1.number_input("Âge",10,100,int(D("age",30) or 30))
        sex=c1.selectbox("Sexe",["Non renseigné","Femme","Homme","Autre"])
        hrmax=c2.number_input("FC max",100,230,int(D("hr_max",190) or 190))
        hrrest=c2.number_input("FC repos",25,120,int(D("hr_rest",50) or 50))
        vma=c2.number_input("VMA",0.,30.,float(D("vma",0) or 0),.1)
        cs=c3.number_input("Vitesse critique",0.,30.,float(D("critical_speed",0) or 0),.1)
        weekly_sessions=c3.number_input("Séances / semaine",1,12,int(D("weekly_sessions",4) or 4))
        weekly_km=c3.number_input("Km run / semaine",0.,250.,float(D("weekly_km",30) or 30))
        yrs=c3.number_input("Années d'entraînement",0.,30.,float(D("training_years",1) or 1),.5)

        goal_type=st.selectbox("Type d'objectif",["RUN","HYROX"],index=0 if D("goal_type","RUN")=="RUN" else 1)
        goal_name=st.text_input("Course / objectif",D("goal_name","Semi-marathon"))
        try:gd=pd.to_datetime(D("goal_date",str(date.today()))).date()
        except:gd=date.today()
        goal_date=st.date_input("Date de course",gd)
        weaknesses=st.multiselect("Points faibles",
            ["Endurance","Seuil","VO₂max","Vitesse","Économie","Sortie longue","Sled Push","Sled Pull","Wall Balls","Running compromis","SkiErg","RowErg","Force"],
            default=loads(D("weaknesses_json"),[]))

        if st.form_submit_button("Enregistrer") and name:
            c=db()
            if row:
                c.execute("""UPDATE athletes SET name=?,age=?,sex=?,hr_max=?,hr_rest=?,vma=?,critical_speed=?,weekly_sessions=?,weekly_km=?,training_years=?,goal_type=?,goal_name=?,goal_date=?,weaknesses_json=?,updated_at=? WHERE id=?""",
                (name,age,sex,hrmax,hrrest,vma,cs,weekly_sessions,weekly_km,yrs,goal_type,goal_name,str(goal_date),json.dumps(weaknesses),datetime.now().isoformat(),int(row["id"])))
            else:
                c.execute("""INSERT INTO athletes(name,age,sex,hr_max,hr_rest,vma,critical_speed,weekly_sessions,weekly_km,training_years,goal_type,goal_name,goal_date,weaknesses_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (name,age,sex,hrmax,hrrest,vma,cs,weekly_sessions,weekly_km,yrs,goal_type,goal_name,str(goal_date),json.dumps(weaknesses),datetime.now().isoformat(),datetime.now().isoformat()))
            c.commit();c.close();st.success("Profil enregistré.")

    A=athletes()
    if not A.empty:
        st.dataframe(A[["id","name","goal_type","goal_name","goal_date","vma","hr_max","weekly_sessions","weekly_km"]],use_container_width=True)

with tabs[1]:
    A=athletes()
    if A.empty:st.info("Crée d'abord un athlète.")
    else:
        aid=st.selectbox("Athlète",A.id,format_func=lambda x:A.loc[A.id==x,"name"].iloc[0],key="planner")
        a=A[A.id==aid].iloc[0].to_dict()
        st.write(f"**Niveau estimé : {athlete_tier(a)}**")
        st.write(f"**Objectif : {a['goal_name']} · {a['goal_date']}**")
        if st.button("Générer le plan"):
            plan=build_plan(a)
            c=db();c.execute("INSERT INTO plans(athlete_id,created_at,plan_json) VALUES(?,?,?)",(int(aid),datetime.now().isoformat(),json.dumps(plan)));c.commit();c.close()
            st.success("Plan généré.")

with tabs[2]:
    A=athletes()
    if not A.empty:
        aid=st.selectbox("Athlète",A.id,format_func=lambda x:A.loc[A.id==x,"name"].iloc[0],key="view")
        lp=latest_plan(int(aid))
        if not lp:
            st.info("Pas encore de plan.")
        else:
            plan=loads(lp["plan_json"],[])
            for block in plan:
                st.markdown(f"## Bloc : {block['name']} — {block['duration']} semaines")
                st.markdown("<div class='info'>Chaque bloc dure 4 à 6 semaines. Les semaines d'assimilation réduisent le volume pour consolider les adaptations.</div>",unsafe_allow_html=True)
                for w in block["weeks"]:
                    title=f"Semaine {w['week']}"
                    if w["assimilation"]: title+=" — Assimilation"
                    with st.expander(title,expanded=w["week"]<=2):
                        for s in w["sessions"]:
                            st.markdown(f"### {s['day']}")
                            if s["type"]=="RUN":
                                render_run(s["content"])
                            elif s["type"]=="MUSCU":
                                render_strength(s["title"],s["details"])
                            else:
                                st.markdown(f"### {s['title']}")
                                st.write(s["details"])
                            if s.get("assimilation"):
                                st.markdown(f"<div class='warn'>{s['assimilation']}</div>",unsafe_allow_html=True)
