# Fitly — guide de test complet (≈ 45 min)

Toutes les commandes se lancent dans PowerShell, à la racine du projet :

```powershell
cd C:\chemin\vers\fitly
$py = ".\.venv\Scripts\python.exe"
```

**Règle d'or :** les étapes 1 à 7 se font dans le **bac à sable**, une copie de ta base. Tu peux y cliquer « J'ai envoyé », créer des relances ou des entretiens : ta vraie base n'est pas touchée. Tu effaces tout à la fin.

Pour chaque étape, coche ✅ ou note ce qui cloche (capture d'écran ou message d'erreur), puis ouvre une issue sur GitHub pour chaque problème.

---

## 0. Vérification automatique (2 min)

```powershell
& $py -m pytest -q
& $py -m fitly doctor
```

Attendu : tous les tests passent (`passed`), puis `RESULT: READY`.
Si tu vois « 4 skipped », lance `& $py -m pytest -q -rs` : la ligne `SKIPPED` indique la raison (en général, le navigateur Chromium de Playwright est absent : `& $py -m playwright install chromium`).

## 1. Ouvrir le bac à sable

```powershell
.\scripts\sandbox.ps1
```

Attendu : le navigateur s'ouvre sur http://localhost:8502, avec un **bandeau jaune « BAC À SABLE »** dans la colonne de gauche.
Laisse cette fenêtre PowerShell ouverte ; pour arrêter, fais Ctrl+C.

## 2. Tableau de bord

- [ ] Six compteurs en haut : nouvelles offres, offres pertinentes (≈ 134), doublons écartés, via ATS, Easy Apply, relances dues (0).
- [ ] Le « ? » à côté d'un compteur affiche son explication.

## 3. Onglet 📋 Offres — tri GO / NO-GO

- [ ] Bouge le curseur « Score minimum » : le nombre d'offres change.
- [ ] Choisis une entreprise qui a beaucoup d'offres, puis tape « architect » dans la recherche : la liste se filtre.
- [ ] La colonne « pourquoi » explique chaque score, par ex. `Data Engineer | role +47, skills +15, loc +15`.
- [ ] Coche **2 offres** : le bouton affiche « GO (2) ». Clique dessus : message de confirmation, et les offres disparaissent de la liste.
- [ ] Coche 1 offre, écris une note (« trop loin »), puis **NO-GO**.
- [ ] Ouvre « 🔎 Détail d'une offre » : fiche complète avec un extrait de l'annonce.

## 4. Onglet ✍️ Candidatures

Ouvre une des offres passées en GO.

- [ ] La fiche affiche le CV à joindre, **avec l'alerte « FILE MISSING »** tant que tes PDF ne sont pas dans `cvs\`, ainsi que l'expérience exigée.
- [ ] Clique sur **✨ Préparer message et réponses**, après avoir ajouté une question (ex. « Pourquoi nous rejoindre ? ») :
  - **sans clé API**, tu obtiens un brouillon à trous `[À COMPLÉTER]` et le message « No Anthropic credentials » ;
  - **avec clé**, voir l'étape 8.
- [ ] Clique sur « Marquer « préparée » » : le pastille passe du vert au jaune.
- [ ] Dans le formulaire **« J'ai envoyé ma candidature »**, mets une date **il y a 10 jours** et un temps passé de 20 min, puis enregistre. L'offre quitte l'onglet.

**Test du garde-fou anti-doublon :**

- [ ] Dans Offres, trouve une entreprise qui publie le même poste (« Data Engineer » par ex.) dans plusieurs villes. Passe-en un en GO, puis enregistre-le comme envoyé.
- [ ] Passe en GO la même offre de cette entreprise, dans une autre ville. Dans Candidatures, un **cadre rouge « Candidature déjà envoyée… »** doit apparaître, et l'envoi est refusé tant que tu ne coches pas « Poste différent ».

## 5. Onglet 📬 Suivi

- [ ] La candidature datée d'il y a 10 jours apparaît dans **« Relances dues »**, avec un modèle de message à copier, et le compteur « Relances dues » passe à 1.
- [ ] Clique sur « Relance envoyée » : elle disparaît, et la prochaine relance est planifiée à J+7.
- [ ] Dans « Mettre à jour une candidature », choisis-la, passe-la en **entretien** et enregistre.

## 6. Onglet 📈 Statistiques

- [ ] Candidatures envoyées, taux de réponse, taux d'entretien et **entretiens / heure de travail** sont calculés à partir de ce que tu viens de faire.
- [ ] Le graphique « Offres pertinentes par source » s'affiche.

## 7. Ligne de commande (même bac à sable)

Ouvre **une deuxième** fenêtre PowerShell :

```powershell
cd C:\chemin\vers\fitly
$py = ".\.venv\Scripts\python.exe"
$env:FITLY_DB = "data\sandbox\fitly.db"     # IMPORTANT : la CLI travaille sur la copie
& $py -m fitly --help
& $py -m fitly metrics
& $py -m fitly followups --drafts          # modèles écrits dans data\followups\
& $py -m fitly answers 12 -q "Quelle est votre disponibilité ?"
& $py -m fitly pack 12                     # fiche écrite dans data\sandbox\candidate_pack_12.md
& $py -m fitly sources
& $py -m fitly probe "Nom de l'entreprise"  # trouve son tableau d'offres (sans l'ajouter)
```

- [ ] Chaque commande répond sans erreur.

## 8. Réponses IA (si tu as une clé API Anthropic)

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."     # pour cette fenêtre seulement
& $py -m fitly answers 12 -q "Pourquoi ce poste ?"
```

- [ ] Le fichier `data\answers\answers_12.md` contient un vrai message, sans fait inventé. Tout ce qui ne figure pas dans `config\local\candidate_facts.md` doit apparaître en `[À COMPLÉTER]`.
- [ ] La ligne « AI budget this month » augmente d'environ 0,05 €.

Complète d'abord `config\local\candidate_facts.md` avec tes vraies expériences : la qualité des réponses en dépend.

## 9. Pré-remplissage d'un vrai formulaire (5 min, sans envoi)

1. Dans l'interface, onglet **⚙️ Mes infos** : remplis prénom, nom, e-mail, téléphone et LinkedIn, puis **💾 Enregistrer mes coordonnées**. Aucun fichier à éditer à la main.
2. Dépose au moins `cvs\cv_tech_fr.pdf`.
3. Choisis une offre **Lever** (lien `jobs.lever.co`) et lance :
   ```powershell
   & $py -m fitly.apply.launch_review --job <ID>
   ```

Attendu :
- [ ] un navigateur s'ouvre sur l'offre et les champs connus sont remplis ;
- [ ] le terminal liste ce qui reste à toi : questions inconnues, consentements ;
- [ ] **rien n'est envoyé**. Ferme sans cliquer sur « Submit », puis appuie sur Entrée dans le terminal.

Si l'outil s'arrête avec « STOP », c'est normal pour une question obligatoire qu'il ne connaît pas. Note laquelle.

> Sur Lever, le formulaire est souvent sur la page `…/apply`. Si rien n'est détecté, relance avec `--url "<lien de l'offre>/apply"`.

## 10. Alertes LinkedIn

1. Crée une alerte d'emploi sur LinkedIn (ex. « Data Engineer », Paris) si tu n'en as pas.
2. Dans Gmail, ouvre un e-mail d'alerte, puis ⋮ → **Télécharger le message**. Place le fichier `.eml` dans `data\inbox\`.
3. Dans le bac à sable, colonne de gauche → **🔄 Collecter les offres maintenant**.

- [ ] Les offres LinkedIn de l'e-mail apparaissent dans Offres, avec un **titre, une entreprise et un lieu corrects**, et le canal `linkedin_check`.
- [ ] En bac à sable, le fichier reste dans `data\inbox\`, pour ta vraie collecte du lendemain.

**Si un champ est faux, ouvre une issue avec ce fichier `.eml` anonymisé** : le lecteur n'a été testé qu'avec des e-mails reconstitués.

## 11. Routine réelle et Excel (sur ta vraie base)

```powershell
.\scripts\morning_review.ps1
```

- [ ] Résultat ≈ 1 400 à 1 500 offres collectées, `errors: 0`, en 2 à 3 minutes.
- [ ] Double-clique sur `data\review_queue.csv` : Excel l'ouvre **en colonnes**.
- [ ] Laisse ce CSV **ouvert** dans Excel et relance `& $py -m fitly review` : un avertissement s'affiche et un fichier `review_queue_<date>.csv` est créé, sans plantage.

Le tri des vraies offres se fait ensuite dans l'interface normale :

```powershell
.\scripts\start_ui.ps1        # http://localhost:8501, sans bandeau jaune = vraies données
```

## 12. Nettoyage

```powershell
.\scripts\sandbox.ps1 -Reset                     # supprime la copie
Remove-Item Env:FITLY_DB                      # dans la fenêtre CLI de l'étape 7
```

---

### Si quelque chose ne marche pas

Copie-moi :
- l'étape ;
- ce que tu as fait ;
- ce que tu as vu (message d'erreur complet, ou capture d'écran).

En bac à sable, rien n'est irréversible : `.\scripts\sandbox.ps1` recrée une copie propre.
