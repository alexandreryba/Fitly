# Fitly — connecter les sources d'offres, pas à pas

Toutes les clés restent **sur ton PC**, dans des variables d'environnement Windows. Elles ne sont jamais écrites dans un fichier du projet, ni envoyées sur GitHub.

## ⚡ Le plus simple : l'assistant de configuration

```powershell
.\scripts\configurer_sources.ps1
```

Choisis la source dans le menu. L'assistant ouvre la bonne page d'inscription, te demande de **coller la clé** (rien ne s'affiche, c'est normal), l'enregistre dans Windows et **teste la connexion aussitôt**. Le détail manuel ci-dessous n'est utile qu'en cas de souci.

**Le principe est le même pour chaque source :**
1. Tu crées un compte gratuit sur le site du fournisseur et tu récupères ta clé.
2. Tu l'enregistres dans PowerShell avec `setx NOM "valeur"`.
3. Tu **fermes et rouvres PowerShell**, et tu relances l'interface (`.\scripts\start_ui.ps1`) : une variable créée avec `setx` n'est visible que dans les nouvelles fenêtres.
4. Tu vérifies :
   ```powershell
   .\.venv\Scripts\python.exe -m fitly check-sources
   .\.venv\Scripts\python.exe -m fitly check-sources --live
   ```
   La première commande dit quelles clés sont présentes. La seconde fait **une** vraie petite requête par source.

Ces sources sont déjà dans ta configuration en mode automatique : elles s'activent toutes seules à la collecte suivante, dès que la clé existe.

---

## 1. Google Jobs (via SerpApi) — le plus utile

Google Jobs regroupe les offres de LinkedIn, Indeed, HelloWork, Welcome to the Jungle, des sites carrières… Google n'a pas d'API publique ; **SerpApi** est un service payant qui l'interroge légalement, avec une offre gratuite mensuelle (vérifie le nombre de recherches incluses sur leur page *Pricing*).

1. Va sur **https://serpapi.com** → *Register*, et crée un compte (e-mail et mot de passe, ou Google).
2. Confirme ton e-mail. Si un numéro de téléphone est demandé, saisis-le toi-même.
3. Dans ton tableau de bord (*Dashboard* ou *Api Key*), copie **Your Private API Key**.
4. Dans PowerShell :
   ```powershell
   setx SERPAPI_KEY "colle-ta-clé-ici"
   ```
5. Ferme et rouvre PowerShell, puis : `.\.venv\Scripts\python.exe -m fitly check-sources --live`.

**Budget :** Fitly fait au plus **3 recherches par collecte** (`max_searches` dans `config\ats_sources.json`), soit environ 60 à 90 par mois si tu collectes chaque jour ouvré. Si ton offre gratuite inclut moins de recherches, mets `"max_searches": 2`, ou collecte un jour sur deux.

## 2. Adzuna (gratuit)

Agrégateur de nombreux sites d'emploi français.

1. Va sur **https://developer.adzuna.com** → *Register*.
2. Une fois connecté, ouvre *Dashboard* → *API Access Details*. Tu y trouves **Application ID** et **Application Key**.
3. Dans PowerShell :
   ```powershell
   setx ADZUNA_APP_ID "ton-application-id"
   setx ADZUNA_APP_KEY "ton-application-key"
   ```
4. Rouvre PowerShell, puis lance `check-sources --live`.

## 3. Jooble (gratuit, sur demande)

1. Va sur **https://jooble.org/api/about** et remplis le formulaire de demande de clé (e-mail, usage : « recherche d'emploi personnelle »).
2. La clé arrive **par e-mail** (parfois après quelques heures).
3. Dans PowerShell :
   ```powershell
   setx JOOBLE_API_KEY "ta-clé"
   ```

## 4. France Travail (officiel, gratuit)

Il reprend aussi des offres de sites partenaires (dont certains grands sites d'emploi).

1. Va sur **https://francetravail.io** → *Se connecter* → *Créer un compte*.
2. Dans ton espace, ouvre *Mes applications* → *Créer une application* (nom : « Fitly », usage personnel).
3. Dans l'application, **ajoute l'API « Offres d'emploi v2 »**. L'accès est en général immédiat.
4. Copie l'**identifiant client** et la **clé secrète**.
5. Dans PowerShell :
   ```powershell
   setx FT_CLIENT_ID "ton-identifiant-client"
   setx FT_CLIENT_SECRET "ta-clé-secrète"
   ```

## 5. Alertes e-mail (LinkedIn, WTTJ, HelloWork, Indeed, APEC, Cadremploi, JobTeaser…)

Ces sites n'ont pas d'API publique et interdisent l'extraction automatique. Leurs **alertes e-mail** sont la voie légale.

### a) Créer les alertes sur chaque site

| Site | Comment |
|---|---|
| LinkedIn | Onglet *Emplois*, fais une recherche (ex. « Data Engineer », Paris), puis active **« Activer l'alerte »** / *Set alert*. Fréquence : quotidienne, par e-mail. Tu peux en créer jusqu'à 20 (une par famille de métier). |
| Welcome to the Jungle | Crée un compte, fais une recherche, puis **« Créer une alerte »**. |
| HelloWork, Indeed, APEC, Cadremploi | Compte gratuit, recherche, puis « Créer une alerte e-mail ». |
| JobTeaser | Avec ton compte d'ancien élève, active les alertes d'offres. |

### b) Faire lire ces e-mails par Fitly — deux options

**Option A, manuelle et sans configuration :** dans Gmail, ouvre une alerte → ⋮ → **Télécharger le message**, puis place le fichier `.eml` dans `data\inbox\`. Il est lu à la collecte suivante.

**Option B, automatique (lecture seule de ta boîte Gmail) :**
1. Active la **validation en deux étapes** sur ton compte Google, si ce n'est pas déjà fait.
2. Va sur **https://myaccount.google.com/apppasswords**, crée un mot de passe d'application nommé « Fitly », et copie les 16 caractères.
3. Dans PowerShell :
   ```powershell
   setx FITLY_IMAP_PASSWORD "les16caracteres"
   ```
4. Dans `config\local\email_alerts.json`, mets `"enabled": true` et `"user": "ton.adresse@gmail.com"`.
5. Lance `check-sources` : la ligne IMAP doit afficher « mot de passe présent ».

Fitly ouvre ta boîte en **lecture seule** : il ne marque rien comme lu, ne déplace rien et ne supprime rien. Il ne lit que les e-mails des expéditeurs d'alertes.

> Le lecteur d'alertes a été testé sur des e-mails reconstitués. Si le titre, l'entreprise ou le lieu ressortent mal sur une vraie alerte LinkedIn ou WTTJ, ouvre une issue en joignant le fichier `.eml` après en avoir retiré tes données personnelles.

## 6. Capture assistée : le bouton « Ajouter à Fitly » (LinkedIn, Indeed, WTTJ, HelloWork, APEC, Cadremploi)

Tu navigues **toi-même**, avec **ton** compte, sur ces sites. Quand une page t'intéresse (une offre, ou une page de résultats de recherche), un clic sur le bouton de l'extension l'ajoute à Fitly. L'offre est alors notée, soumise à la règle par entreprise, proposée dans la Sélection du jour et peut être pré-remplie.

L'extension ne fait rien toute seule :
- elle ne lit la page que **quand tu cliques** ;
- elle n'envoie les données qu'à Fitly, **sur ton PC** (`127.0.0.1`) ;
- elle n'automatise aucune navigation.

### Installation dans Chrome (une seule fois, environ 2 min)
1. Ouvre Chrome et tape **`chrome://extensions`** dans la barre d'adresse.
2. Active **« Mode développeur »** (interrupteur en haut à droite). Si l'extension Fitly est déjà là, clique simplement sur son bouton **↻ (recharger)** et passe à l'étape 4.
3. Clique sur **« Charger l'extension décompressée »**, puis choisis le dossier
   `C:\chemin\vers\fitly\extension`.
4. L'extension « Fitly - Ajouter à Fitly » apparaît. Clique sur l'icône **puzzle** 🧩 à droite de la barre d'adresse, puis sur l'**épingle** à côté de Fitly pour garder le bouton visible.

### Utilisation
1. Lance l'interface : `.\scripts\start_ui.ps1`. Dans la colonne de gauche, tu dois voir « 🧩 Extension … : prête ».
2. Sur LinkedIn, Indeed, WTTJ, HelloWork, APEC ou Cadremploi, ouvre une **offre** ou une **page de résultats**.
   Sur une page de résultats, fais défiler la liste pour que toutes les offres soient chargées.
3. Clique sur le bouton Fitly, puis **« Ajouter cette page à Fitly »**. Le message indique le nombre d'offres ajoutées et les 3 meilleures.
4. Dans l'interface, filtre la colonne **Source** sur « capture » pour retrouver ces offres.

Astuce : une page d'offre détaillée donne la description complète, donc un meilleur score. Une page de résultats donne seulement le titre, l'entreprise et le lieu, mais en une fois.

Si un site change sa présentation et que plus rien n'est reconnu, ouvre une issue avec l'adresse de la page : la lecture de ce site sera à ajuster.

## 7. Ajouter une entreprise précise

```powershell
.\.venv\Scripts\python.exe -m fitly probe "Nom de l'entreprise" --add
```

Fitly cherche son tableau d'offres public (Lever, Greenhouse, Ashby, SmartRecruiters, Workable, Recruitee, Teamtailor) et l'ajoute à tes sources.

---

## 8. Postuler à une offre en GO, de façon automatisée

1. Dans l'interface, onglet **✍️ Candidatures (GO)**, ouvre l'offre. La fiche affiche le CV à joindre et la commande à lancer.
2. *(Optionnel)* **✨ Préparer message et réponses** : brouillon de message et réponses aux questions que tu colles (IA si clé Anthropic, sinon modèle à trous).
3. Lance la commande affichée, par exemple :
   ```powershell
   .\.venv\Scripts\python.exe -m fitly.apply.launch_review --job 1234
   ```
4. Edge s'ouvre sur le formulaire de l'entreprise. Fitly :
   - **remplit** tes coordonnées (prénom, nom, e-mail, téléphone, LinkedIn, ville, pays…) et **joint le CV** recommandé ;
   - **liste dans le terminal** ce qu'il te reste à faire : questions propres à l'entreprise, cases de consentement ;
   - **s'arrête** sur un CAPTCHA ;
   - **ne clique jamais** sur « Envoyer ».
5. Tu complètes ce qui reste, tu relis, puis **tu cliques toi-même** sur « Envoyer » sur le site.
6. Dans Fitly : **« J'ai déjà envoyé ma candidature → l'enregistrer »** (date, CV, temps passé). La relance est planifiée à J+7.

### Ce qui se passe selon le type d'offre (bandeau en haut de l'onglet)

| Cas | Ce que fait Fitly | Ce que tu fais |
|---|---|---|
| **Formulaire direct** (Lever, Greenhouse, Ashby, Teamtailor, Workable, Recruitee…) | ✅ Ouvre le formulaire (« Postuler », « Je suis intéressé(e) »…), remplit, coche les cases obligatoires (jamais newsletter) | Tu complètes les questions propres à l'entreprise, puis **tu envoies** |
| **SmartRecruiters** | 🧩 Clique « Je suis intéressé(e) » ; le site affiche une **vérification anti-robot (CAPTCHA)**. Fitly ne la résout et ne la contourne **jamais** : il attend | Tu résous le CAPTCHA (3 min max) → le formulaire est **pré-rempli automatiquement** ensuite |
| **Offre relayée par un site d'emploi** (LinkedIn, Indeed, APEC, Figaro Emploi, BeBee… via Google Jobs) | 🧭 N'automatise **rien** sur ces sites (conditions d'utilisation) ; affiche un bandeau | Tu cliques **Postuler** : l'onglet du site de l'entreprise qui s'ouvre est **pré-rempli automatiquement** (l'extension suit la redirection, nouvel onglet compris) |
| **Connexion requise** (compte sur le site) | ⚠️ « aucun formulaire reconnu » | Tu te connectes ; recharge l'onglet si besoin |

Pour les nouvelles offres Google Jobs, Fitly retient désormais en priorité le lien **du site de l'entreprise** plutôt que celui d'un site d'emploi : davantage d'offres arrivent directement sur un formulaire pré-remplissable.

### Vérification sur le site de l'employeur (chaque matin, ou bouton 🔎 dans la barre latérale)

Pour les meilleures offres, Fitly cherche **la même offre sur le site carrière de l'entreprise** (API publiques Lever, Greenhouse, Ashby, SmartRecruiters, Workable, Recruitee, Teamtailor) :

| Colonne « dispo » | Signification |
|---|---|
| ✅ lien employeur trouvé | L'offre relayée existe sur le site de l'entreprise : c'est **ce lien** qui est ouvert et pré-rempli |
| 🧭 via site d'emploi | Pas de site carrière public trouvé : clique sur Postuler dans l'onglet |
| ⚠️ absente du site carrière | L'entreprise a un site carrière en France mais l'offre n'y est plus : probablement pourvue (exclue de la sélection du jour) |
| 🚫 expirée | Page de l'offre disparue ou « offre plus disponible » : **retirée de la liste** |

Les sites d'emploi (LinkedIn, Indeed, APEC…) ne sont **jamais** interrogés automatiquement.

### Entreprises écartées

Onglet **Mes infos → Entreprises où ne pas postuler** (vide par défaut). Leurs offres disparaissent de la liste et de la sélection du jour.

### Remplissage hybride Google + Fitly (réglage Chrome, une seule fois)

1. **Classification des champs par Google** : ouvre `chrome://flags`, cherche **« autofill predictions »** (Show autofill predictions), mets **Enabled**, puis clique **Relaunch**. Chrome indique alors sur chaque champ ce qu'il a reconnu (prénom, nom, e-mail, téléphone, ville…) et Fitly s'en sert pour remplir les champs mal étiquetés.
2. **Ton profil Chrome** : `chrome://settings/addresses` → **Ajouter** : nom, e-mail, téléphone, ville. Sur un champ resté vide, clique dedans et choisis ton profil : Chrome complète le reste du bloc.
3. Fitly **ne remplace jamais** un champ déjà rempli (par Chrome ou par toi) ; il ajoute le CV, les questions standard (salaire, disponibilité, visa…) et coche les cases obligatoires (jamais newsletter).

### Test à grande échelle sans rien saisir (audit)

```powershell
.\.venv\Scripts\python.exe -m fitly.apply.prefill_audit --n 50
```

Ouvre 50 offres de 50 entreprises différentes (une par site carrière, toutes les plateformes représentées) dans un Chrome hors écran, et indique pour chacune ce que Fitly **remplirait** (champs reconnus, questions obligatoires restantes, CAPTCHA, connexion requise, offre expirée). **Rien n'est saisi ni envoyé.** Résultat : `data\prefilludit.json` et captures dans `data\prefilludit\`.

Statut 🔐 **connexion requise** : le site demande un compte (Workday, Taleo, Indeed…). Connecte-toi dans l'onglet : le formulaire qui suit est pré-rempli.

### Pré-remplissage en un clic (n'importe quelle page de candidature)

Quand le pré-remplissage automatique n'a rien fait (site qui redirige, formulaire derrière plusieurs étapes…) :
1. clique toi-même sur « Postuler » et avance jusqu'au formulaire ;
2. clique sur le bouton vert **✍️ Pré-remplir** qui apparaît en bas à gauche des pages de candidature
   (ou **Alt+Maj+P**, ou bouton de l'extension → ✍️ Pré-remplir cette page) ;
3. formulaire en plusieurs pages : refais Alt+Maj+P à chaque étape.

Fitly reconnaît l'offre si elle est dans ta base (CV conseillé), sinon il utilise tes infos et ton CV par défaut (tech_fr). Les formulaires intégrés dans un cadre (Greenhouse, Workday, Taleez… dans le site de l'entreprise) sont aussi remplis. Rien n'est envoyé ; jamais rien sur LinkedIn, Indeed et autres sites d'emploi, ni dans une vérification anti-robot. Raccourci modifiable dans `chrome://extensions/shortcuts`.

Pour avoir l'icône Fitly toujours visible : icône 🧩 (puzzle) à droite de la barre d'adresse → épingle 📌 à côté de Fitly (Chrome ne permet pas à une extension de s'épingler elle-même).

### Mise à jour de l'extension

L'extension **se recharge toute seule** quand Fitly est mis à jour (l'onglet se recharge une fois).

**Une seule exception : le passage à Fitly 1.1.** L'identifiant de l'extension devient fixe (`edpcldibebibfflchgbodmbhnpihggdl`) et Fitly n'accepte plus que lui. Réinstalle-la une fois : `chrome://extensions` → carte Fitly → **Supprimer**, puis **« Charger l'extension décompressée »** → le dossier `extension`. Tant que ce n'est pas fait, l'ancienne extension reçoit « Origine refusée ».

---

## 9. Les meilleurs sites pour des offres data / IA à Paris, et leur intégration

| Site | Intérêt pour ton profil | Intégré à Fitly ? | Comment |
|---|---|---|---|
| **Sites carrières des entreprises** (Lever, Greenhouse, Ashby, SmartRecruiters, Workable, Recruitee, Teamtailor) | ★★★ Offres à la source, souvent les plus fraîches | ✅ **Oui, direct** | Ta liste d'entreprises, ajoutées avec `probe --add` (section 7) et gardées dans `config\local\` |
| **LinkedIn** | ★★★ Le plus large, beaucoup de data/IA | ✅ **Capture assistée** + alertes e-mail | Section 6 (bouton) et section 5 ; aussi via Google Jobs |
| **Welcome to the Jungle** | ★★★ Startups et scale-ups tech, très data | ✅ **Capture assistée** + alertes e-mail | Section 6 (bouton) et section 5 ; aussi via Google Jobs |
| **Google Jobs** | ★★★ Agrège LinkedIn, Indeed, HelloWork, WTTJ… | 🟡 **Prêt, clé à créer** | Section 1 (SerpApi) |
| **Indeed** | ★★ Très large, plus généraliste | ✅ **Capture assistée** + alertes e-mail | Section 6 (bouton) et section 5 ; aussi via Google Jobs |
| **APEC** | ★★ Postes cadres, grands groupes | ✅ **Capture assistée** + alertes e-mail | Section 6 (bouton) et section 5 ; aussi via Google Jobs |
| **HelloWork** | ★★ Généraliste français | ✅ **Capture assistée** + alertes e-mail | Section 6 (bouton) et section 5 ; aussi via Google Jobs |
| **France Travail** | ★★ Officiel, inclut des offres partenaires | 🟡 **Prêt, clé à créer** | Section 4 |
| **Adzuna / Jooble** | ★★ Agrégateurs (beaucoup d'offres, parfois doublonnées) | 🟡 **Prêts, clés à créer** | Sections 2 et 3 |
| **JobTeaser** | ★★ Offres junior, réseau de ton école | 🟡 **Via alertes e-mail** | Section 5 (avec ton compte d'école) |
| **Cadremploi** | ★★ Cadres, conseil et grands groupes | ✅ **Capture assistée** + alertes e-mail | Section 6 (bouton) et section 5 ; aussi via Google Jobs |
| **Free-Work** | ★★ IT et data, CDI et freelance | 🟡 **Via alertes e-mail** | Section 5 |
| **talent.io** | ★★★ Les entreprises te contactent (profil tech) | ❌ **Non** (pas d'offres publiques) | Crée un profil ; les propositions reçues s'ajoutent à Fitly par import CSV |
| **Malt** | ★ Missions freelance | ❌ Non | Seulement si le freelance t'intéresse ; profil sur Malt |
| **Wellfound** (ex-AngelList) | ★ Startups, surtout internationales | 🟡 Via alertes e-mail | Section 5 |
| **Glassdoor** | ★ Avis et salaires, plus que les offres | 🟡 Via alertes e-mail | À utiliser surtout pour te renseigner avant un entretien |
| **Réseaux d'anciens** (écoles, anciens employeurs), cooptation | ★★★ Le meilleur taux de réponse | ❌ Non (humain) | Note les pistes dans Fitly : import CSV, ou GO avec une note |

**Légende :** ✅ automatique · 🟡 automatique une fois l'alerte ou la clé configurée · ❌ hors outil.

### Recommandations pour démarrer, par ordre d'impact
1. **Google Jobs (SerpApi)** : une clé, 5 minutes, et tu couvres une grande partie de LinkedIn, Indeed, HelloWork et WTTJ d'un coup.
2. **Alertes LinkedIn et WTTJ**, avec l'option B (IMAP) : les offres arrivent chaque matin sans rien faire.
3. **France Travail et Adzuna** : gratuits, ils complètent la couverture.
4. **talent.io et ton réseau d'anciens** : hors outil, mais ce sont souvent les canaux qui donnent le plus d'entretiens. C'est l'indicateur que Fitly cherche à maximiser.
