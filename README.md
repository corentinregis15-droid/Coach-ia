# Coach IA Cloud V2

Application Streamlit pensée pour être hébergée et utilisée depuis un iPad dans Safari.

## Formats importés
- FIT
- TCX
- GPX
- CSV

## Fonctionnalités
- fiche athlète complète ;
- FC max / FC repos, VMA, vitesse critique, FTP ;
- records run / HYROX ;
- max musculation ;
- tests SkiErg et RowErg 2 km ;
- séance prévue ;
- import de séance ;
- calcul FC / puissance / cadence / distance / vitesse ;
- calculs simples : dérive cardiaque, variabilité puissance ;
- RPE, sensations, fatigue musculaire/générale, douleur ;
- rapport coach ;
- historique.

## Déploiement sur iPad
Cette application doit être hébergée. La solution la plus simple est Streamlit Community Cloud :
1. Mettre ces fichiers dans un dépôt GitHub.
2. Aller sur share.streamlit.io.
3. Créer une app avec `app.py` comme fichier principal.
4. Ouvrir l'URL `.streamlit.app` dans Safari.
5. Optionnel : Safari > Partager > Sur l'écran d'accueil.

Les dépendances de requirements.txt sont installées automatiquement par Streamlit Cloud.
