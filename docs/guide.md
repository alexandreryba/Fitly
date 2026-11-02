# Fitly — guide pas à pas, sans aucun risque

Ce guide te fait découvrir, dans l'ordre :

- **A.** ce qui peut ou ne peut pas partir vers une entreprise ;
- **B.** tes informations personnelles : quoi, pourquoi, comment ;
- **C.** le circuit GO / NO-GO, la règle par entreprise et la réserve ;
- **D.** le module qui prépare automatiquement une candidature, d'abord sur un **formulaire d'exercice** ;
- **E.** les fonctions que tu n'as pas encore testées.

Durée : environ 1 h. Garde ce fichier ouvert à côté de PowerShell.

```powershell
cd C:\chemin\vers\fitly
$py = ".\.venv\Scripts\python.exe"
```

---

## A. La règle d'or : qu'est-ce qui peut partir vers une entreprise ?

| Action dans Fitly | Quelque chose part-il vers une entreprise ? |
|---|---|
| Collecter les offres (`morning_review.ps1`, bouton 🔄) | **Non.** Fitly **lit** des pages d'offres publiques, comme quand tu les consultes. |
| GO, NO-GO, Réserve, Remettre à trier | **Non.** Ce sont des étiquettes dans **ta** base, sur ton PC. |
| « J'ai déjà envoyé ma candidature → l'enregistrer » | **Non.** C'est un **pense-bête** : il note que *toi* tu as postulé, pour planifier la relance. |
| Relances (modèles de message) | **Non.** Fitly écrit un brouillon ; tu le copies et l'envoies toi-même si tu veux. |
| Réponses IA ✨ | **Non** vers l'entreprise. Avec une clé API, ton profil et l'offre sont envoyés à Anthropic pour rédiger un brouillon ; sans clé, rien ne sort. |
| Formulaire d'exercice (`--demo`) | **Non.** C'est un fichier sur ton PC, sans internet. |
| Pré-remplissage sur un **vrai** site (`--job`) | **Aucune candidature n'est envoyée** : Fitly ne clique **jamais** sur « Envoyer », il n'en a même pas la fonction. ⚠️ Un détail : sur certains sites (Lever…), **joindre un CV** le téléverse aussitôt sur le site pour l'analyser. Pour ce test, utilise donc `--sans-cv` : rien ne sort de ton PC. |

> **Le seul geste qui envoie une candidature, c'est toi qui cliques sur le bouton « Submit / Envoyer » d'un vrai site.** Dans ce guide, tu ne le feras jamais.

---

## B. Tes informations personnelles (10 min, une seule fois)

### Pourquoi ?

Le module de pré-remplissage ne remplit **que** des valeurs que tu as validées. Il n'invente rien. Si ton prénom n'est pas renseigné, il s'arrête et te dit « il manque : First Name ».

L'IA, de son côté, ne parle de ton parcours qu'à partir de ce que tu as écrit. Le reste devient `[À COMPLÉTER]`.

### Où ? Dans l'interface, onglet « ⚙️ Mes infos »

```powershell
.\scripts\start_ui.ps1
```

Le navigateur s'ouvre sur http://localhost:8501. Clique sur l'onglet **⚙️ Mes infos** (le dernier à droite).

**1. Bloc « Coordonnées pour les formulaires »**

| Champ | Faut-il le remplir ? | Exemple |
|---|---|---|
| Prénom, Nom, E-mail, Téléphone | **Oui** : demandés par presque tous les formulaires | Camille / Dupont / ton e-mail / 06… |
| URL du profil LinkedIn | Recommandé | https://www.linkedin.com/in/… |
| Ville | Recommandé | Paris |
| Nom complet | Seulement si tu veux le remplir automatiquement | Camille Dupont |
| Prétentions salariales, préavis, années d'expérience, autorisation de travail, sponsoring | **Facultatif.** Si c'est vide, Fitly te laisse ces questions. Ne remplis que ce que tu acceptes de voir saisi automatiquement. | Ton montant brut annuel ; 1 mois ; 3 ; Oui (UE) ; Non |

Clique ensuite sur **💾 Enregistrer mes coordonnées**. Un message confirme le nombre de champs enregistrés. Tout reste sur ton PC, dans `config\local\answer_library.json`.

**2. Bloc « Faits vérifiés pour l'IA »**

Le texte contient déjà ta formation et tes technologies, à vérifier. Remplace chaque `[À COMPLÉTER]` par du **vrai** : entreprise, poste, dates et 2 ou 3 réalisations concrètes par expérience. Clique ensuite sur **💾 Enregistrer les faits**.

C'est facultatif pour tester, mais indispensable pour que les brouillons IA soient utiles.

**3. Bloc « Mes CV »**

Il affiche ❌ pour chaque CV absent. Copie tes PDF dans `C:\chemin\vers\fitly\cvs\` avec **exactement** ces noms :
- `cv_tech_fr.pdf`
- `cv_tech_en.pdf`
- `cv_business_fr.pdf`
- `cv_business_en.pdf`

Les règles sont rappelées dans `cvs\LISEZMOI.txt`. Recharge la page (F5) : les ❌ deviennent ✅.

Ferme ensuite cette fenêtre PowerShell (Ctrl+C). Ces informations sont aussi utilisées par le bac à sable.

---

## C. GO / NO-GO, règle par entreprise et réserve (bac à sable, 20 min)

### Le circuit d'une offre

```
                  ┌──────────────► ⛔ NO-GO ──── ↩️ remettre à trier ───┐
                  │                                                     │
🗂️ Réserve ──► 📋 Offres (à trier) ──► ✅ GO ──► 🟡 préparée ──► 📨 « envoyée »* ──► relancée ──► entretien / offre / refus
   ▲   (quota      │      ▲                │                        │
   │   par         │      └── ↩️ remettre à trier ──────────────────┘ (annuler si enregistré par erreur)
   │   entreprise) │
   └── surplus ────┘
```

\* « envoyée » = **tu** as postulé sur le site de l'entreprise et tu le notes dans Fitly.

**La règle par entreprise :** jusqu'à **5 offres** quand ce sont des variantes du **même poste** (Data Engineer, Senior Data Engineer, Data Engineer Retail, Data Engineer GCP…), et au plus **3 métiers différents** (par ex. Data Engineer, Solution Architect, Analytics Engineer). Tes GO et tes candidatures des 90 derniers jours comptent. Le surplus attend dans 🗂️ **Réserve**, avec la raison. La colonne **métier** montre comment Fitly regroupe les variantes.
- Un **GO** prend une place.
- Un **NO-GO** n'en prend pas : l'offre suivante de la réserve remonte toute seule.

Réglages dans `config\application_rules.json` : `max_offers_per_company` (5), `max_distinct_roles_per_company` (3), `company_window_days` (90).

### Exercice (bac à sable : rien ne touche ta vraie base)

```powershell
.\scripts\sandbox.ps1
```

Vérifie que le bandeau jaune **« BAC À SABLE »** est bien affiché à gauche.

1. **Tableau de bord** — repère « En réserve » et « Tech / Business / FR / EN à trier ».
2. **📋 Offres** — choisis une entreprise qui publie beaucoup d'offres (plus de 5) : au plus **5 offres**, sur 3 métiers au plus (colonne « métier »).
3. **🗂️ Réserve** — les autres offres de cette entreprise sont là, avec la raison (« 5 offres déjà retenues » ou « 3 métiers différents déjà retenus »).
4. Retour à **📋 Offres** → coche **une** offre de cette entreprise → **⛔ NO-GO** (note : « test »).
   ➜ L'entreprise garde le même nombre d'offres : **une offre de la réserve est remontée**.
5. Coche une autre offre de cette entreprise → **✅ GO**.
   ➜ Le GO occupe une place : une offre de moins à trier pour cette entreprise.
6. **⛔ NO-GO** — ton offre « test » y est. Coche-la → **↩️ Remettre à trier** : elle revient dans 📋 Offres.
7. **✍️ Candidatures (GO)** — ouvre ton offre GO. Tu y trouves :
   - la **fiche** : CV à joindre (✅ si ton PDF est déposé), expérience exigée, extrait de l'annonce ;
   - **✨ Préparer message et réponses** : sans clé API, tu obtiens un brouillon à trous `[À COMPLÉTER]`, rien ne sort ;
   - la commande de pré-remplissage, que tu testeras en D ;
   - les boutons **⛔ Passer en NO-GO** et **↩️ Remettre à trier**.
8. **Alerte de cohérence** — chez la même entreprise, passe en GO une offre d'une autre famille (colonne « famille »), par ex. un « Produit » après un « Ingénierie ». Dans Candidatures, un **cadre orange** explique le risque de profil dispersé.
9. **Enregistrer un envoi fictif** (bac à sable uniquement) — dans le formulaire « J'ai déjà envoyé… », mets une date il y a 10 jours, puis **✅ Enregistrer l'envoi**.
10. **📬 Suivi** — la relance est due, avec un modèle de message. Teste « Relance envoyée », puis « Mettre à jour » → entretien.
11. **Annuler** — 📬 Suivi → « ↩️ Annuler une candidature enregistrée par erreur » → coche la confirmation → l'offre revient à trier, et les statistiques sont nettoyées.
12. **🗂️ Réserve → GO hors quota** — coche une offre ; le bouton reste grisé tant que tu n'as pas coché « Je dépasse volontairement le quota ».

Laisse le bac à sable ouvert pour la suite.

---

## D. Le module de candidature automatique

### D1. Exercice 100 % local (5 min)

Ouvre une **deuxième** fenêtre PowerShell :

```powershell
cd C:\chemin\vers\fitly
.\.venv\Scripts\python.exe -m fitly.apply.launch_review --demo ok
```

Ce qui se passe :
1. Une fenêtre **Edge** s'ouvre sur un formulaire marqué **« EXERCICE — formulaire fictif »** (DemoCorp).
2. Fitly remplit prénom, nom, e-mail, téléphone et LinkedIn, et joint ton `cv_tech_fr.pdf`.
3. Il laisse **vide** la question facultative « How did you hear about us? ».
4. Il **ne coche pas** la case de consentement : c'est ta décision.
5. Le terminal affiche « PRÉ-REMPLI : n champ(s) » et « **RIEN N'A ÉTÉ ENVOYÉ** ».

Si tu cliques toi-même sur « Submit application » dans cette page d'exercice, un cadre vert indique « CECI EST UN EXERCICE — rien n'a été envoyé ». C'est le seul geste qui, sur un vrai site, enverrait ta candidature.

Appuie sur **Entrée** dans le terminal pour fermer.

Si tu vois « OBLIGATOIRE, À REMPLIR PAR TOI : First Name », reprends la partie B (Mes infos et CV).

**Deuxième exercice** — un formulaire avec une question obligatoire que Fitly ne connaît pas :

```powershell
.\.venv\Scripts\python.exe -m fitly.apply.launch_review --demo stop
```

Attendu : tes coordonnées sont **pré-remplies**, et le terminal affiche « **OBLIGATOIRE, À REMPLIR PAR TOI** : Why do you want to join DemoCorp? ». Fitly n'invente jamais une réponse à une question propre au formulaire : il te la laisse. Rien n'est envoyé.

> Seuls un **CAPTCHA** ou une **protection anti-robot** arrêtent tout. Pour revenir à l'ancien comportement strict (ne rien remplir dès qu'une question obligatoire est inconnue), mets `"unknown_required_question": "stop"` dans `config\application_rules.json`.

### D2. Optionnel : un vrai formulaire, sans rien envoyer (5 min)

1. Dans le bac à sable, onglet ✍️ Candidatures, prends une offre en GO dont le lien commence par `jobs.lever.co`. Note son **id**.
2. Lance, **avec `--sans-cv`** :
   ```powershell
   .\.venv\Scripts\python.exe -m fitly.apply.launch_review --job <ID> --sans-cv
   ```
3. Edge ouvre la page de candidature de l'entreprise, puis Fitly pré-remplit ce qu'il connaît, ou s'arrête et liste ce qui manque.
4. **Ne clique sur aucun bouton du site.** Ferme la fenêtre, ou appuie sur Entrée dans le terminal.

Rien n'est envoyé : ni candidature, ni CV.

---

## E. Les autres fonctions à explorer (bac à sable)

| Où | Quoi essayer |
|---|---|
| 📋 Offres | Filtre **Publiée** (« 7 derniers jours ») ; filtres **Type** (Business seul) et **Langue** (EN seul) ; colonne **famille** ; panneau 🔎 Détail d'une offre. |
| Colonne de gauche | **📄 Exporter CSV + rapport HTML** (dans le bac à sable, écrit dans `data\sandbox\`). |
| 📈 Statistiques | Taux de réponse, taux d'entretien, entretiens par heure, offres pertinentes par source. |
| CLI (2e fenêtre) | Lance `$env:FITLY_DB = "data\sandbox\fitly.db"`, puis : `& $py -m fitly metrics`, `& $py -m fitly followups --drafts`, `& $py -m fitly probe "Nom de l'entreprise"`, `& $py -m fitly undo <ID>`. |

---

## F. Fin du test

```powershell
.\scripts\sandbox.ps1 -Reset      # efface la copie : ta vraie base n'a jamais été touchée
```

Pour l'usage réel ensuite : `.\scripts\start_ui.ps1`, sans bandeau jaune. Tes GO et NO-GO y seront les vrais, mais ils restent tous réversibles, et rien n'est jamais envoyé sans ton clic sur le site.

### En cas de souci

Ouvre une [issue sur GitHub](https://github.com/alexandreryba/Fitly/issues) avec :
- la lettre et le numéro de l'étape (par ex. « C5 ») ;
- ce que tu attendais ;
- ce que tu as vu (texte du terminal ou capture d'écran).
