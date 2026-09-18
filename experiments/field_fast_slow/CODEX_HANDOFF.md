# Reprise Codex — fast/slow terrain, SO-101 et RTX 3090

## Mandat et limites

Lire d'abord README.md, les rapports `evidence/`, CONTRIBUTING.md,
docs/m3/M3A_TWO_BUTTON.md et docs/concepts/TIME_FRAMES_AND_PROVENANCE.md.
Cette branche ajoute un laboratoire autonome. **Elle n'active pas le matériel,
ne remplace pas M3a, ne clôt aucun jalon M3b et ne prouve pas Jev.**
Ne pas fusionner automatiquement et ne pas modifier Unreal pour ce travail.

Base inspectée : `26783d9cae483dd119179135accdb48829fc4fc6`.
Branche : `experiment/field-fast-slow-so101`.
Priorité : valider une séparation d'autorités et apprendre où un modèle est utile,
pas obtenir à tout prix un meilleur chiffre qu'un contrôleur déterministe.

## 0. Reproduire et auditer avant intégration

Depuis la racine :

```bash
git status --short
python -m pip install -e '.[dev]'
python -m pytest
ruff check .
cd experiments/field_fast_slow
python -m unittest discover -s . -p 'test_*.py' -v
python experiment.py --out results/cpu
python experiment.py --seed 20260928 --out results/confirmation
```

Ne pas installer de dépendances dans l'environnement de production sans accord.
Garder l'environnement ML séparé ; choisir Python 3.11/3.12 pour la validation
contextuelle du dépôt, dont la préparation a ici seulement testé l'expérience
isolée sur Python 3.13.5. Le lint et la suite intégrée restent à exécuter.

Vérifier le diff depuis la base, l'absence de chemin moteur et les empreintes du
code dans les rapports. Ne pas utiliser des poids PyTorch provenant d'une source
inconnue ; ceux générés ici sont recréables localement et ne sont pas committés.
Produire un rapport avec versions, OS, CPU, GPU réel, driver, dépendances et SHA.
Répéter plusieurs graines, dont au moins trois nouvelles après gel du code.
Les deux graines actuelles sont exploratoires. Ne pas ajuster les seuils sur le test.

## 1. Banc 3090, sans bras connecté

Conserver trois comparateurs : règles, petit MLP numérique, tête Qwen3-0.6B
fine-tunée (`text_head.py`). Pour un LLM lent, Qwen3-4B est un candidat, pas une
exigence. Utiliser des poids épinglés par SHA et noter licence/quantification.

La recette Qwen est préparée mais NON testée avec Transformers/PEFT ou CUDA.
Vérifier `AutoModelForSequenceClassification`, la tête `score`, le padding,
les modules LoRA, BF16, les shapes, les gradients et l'absence de `generate()`.
Faire un smoke test minuscule avant l'entraînement complet, puis verrouiller les
versions. Une comparaison sur huit nombres ne démontre aucun avantage sémantique
préentraîné ; elle sert à chiffrer le surcoût et les erreurs de substitution.

Pour étudier la sélection de compétences, créer une vraie taxonomie séparée :
`HOLD`, `REQUEST_OBSERVATION`, `START_ENSURE_LATCHED`, `REQUEST_REPLAN`.
Les labels APPROACH/PRESS actuels ne doivent pas devenir cette API par renommage
implicite. Aucun modèle ne remplace les transitions internes déterministes pour
le premier essai. Les choix latéraux/retour/approche ne sont pas automatiquement
indépendants : valider la combinaison et les préconditions, pas chaque probabilité
isolément. Une softmax, même calibrée, n'accorde jamais un droit d'action.

Mesurer : modèle seul, encodage/texte, H2D, inférence, D2H, filtrage et latence
observation->décision ; p50/p95/p99/max, warm-up, échantillons, VRAM et allocations.
Puis répéter avec le LLM lent générant un contexte long sur la même 3090.
Un processus séparé ou une priorité CUDA n'est pas une garantie de préemption.
Préférer le petit sélecteur sur CPU s'il suffit ; le contrôleur et ses watchdogs
ne doivent jamais attendre une opération GPU ou un appel réseau.

Cibles initiales de conception à tester, NON performances garanties : sélection
10 Hz, commandes hôte 20 Hz seulement après validation du bus ; tick simulé actuel
50 Hz. Les boucles internes des servos ne sont pas remplacées. Le budget utile
se mesure de la capture jusqu'à l'exécution, pas seulement le forward du réseau.

## 2. Intégration M3a en mode observation, sans action

Ne pas remplacer `decide_two_button` par le MLP. Introduire une interface locale
optionnelle de **proposition** à côté du chemin existant, désactivée par défaut.
Le Robot reste seul propriétaire des mouvements, préconditions et effets.
Le Field accepte/coordon­ne les tâches et héberge éventuellement le planificateur ;
l'inférence peut être colocalisée sans lui donner l'autorité du Robot.

Points d'ancrage existants à lire dans leur version courante :

```text
python/src/deferred_teleop/runtime.py
  M3aFieldService / M3aRobotService / M3aMissionService
python/src/deferred_teleop/two_button_policy.py
  decide_two_button / derive_spatial_press_command
python/src/deferred_teleop/m3a_types.py
python/src/deferred_teleop/two_button_fixture.py
python/src/deferred_teleop/storage.py
  réservation durable / propriétaire unique / annulation
```

Les propositions transportent au minimum : opération/révision, affectation,
contrat, request_id, observation source/digest, identité/époque, frame/calibration,
modèle/version et limites temporelles. Elles ne transportent pas de nouveau droit
ni de position à exécuter directement. À la consommation, reprendre l'observation
courante, puis utiliser la politique et la dérivation de commande existantes.

Ne jamais remettre `observed_at` à l'heure de fin d'inférence. Conserver séparés
les temps observé/produit/reçu, et la provenance SIMULATED, MEASURED ou INFERRED.
Les horloges monotones ne sont comparables que dans le même domaine de boot ;
le contrat de l'expérience suppose une horloge locale unique. Pour des processus
ou hôtes distincts, établir explicitement le domaine d'horloge, le boot-id et la
conversion/incertitude, sinon refuser. Ne pas convertir naïvement Mission UTC en
monotone Robot. Ne pas fusionner les états Confirmed, Arrival et Target.

`slow_client.py` donne un producteur à une requête en vol. Exécuter le serveur
modèle dans un autre processus, lié à loopback uniquement. Vérifier le support
réel du schéma JSON ; ne pas exposer de shell/outils moteurs au modèle. Sa réponse
peut être fausse malgré un schéma valide. Une expiration rejette la réponse mais
n'annule pas nécessairement le calcul GPU. Le thread Python fourni n'est pas une
implémentation temps réel dur ni une isolation de sûreté.

Ajouter des tests intégrés : résultat ancien après annulation/révision/boot,
changement de calibration/identité, pose déplacée pendant l'inférence, caméra
figée, modèle bloqué, NaN, JSON en doublon/casse différente, réponse vide,
GPU indisponible et redémarrage après effet. Vérifier que le filtre n'invente pas
une autorisation de mouvement et que le modèle ne consomme pas le budget.
Tester via les stores/journaux existants la non-répétition d'effet : le booléen
`spent` du simulateur n'est PAS un remplacement du budget durable de production.

## 3. Préparer la table SO-101 : observation seule

L'agent ne doit pas deviner les ports, offsets, sens, unités, limites ou gripper.
Découvrir le LeRobot/driver réellement installé, épingler son commit/version,
consulter son API et la calibration locale. L'architecture de base n'a pas encore
de chemin matériel public ; cette branche n'en fournit volontairement aucun.

Identifier bras/firmware et modèle structurel exact ; vérifier la description
SO-101 déjà épinglée dans `robots/` et sa compatibilité mécanique. Reprendre la
cinématique de référence du dépôt plutôt que créer un second référentiel incohérent.
Les valeurs SI du dépôt sont en mètres/radians ; les unités éventuellement
normalisées, degrés ou ticks du driver doivent rester derrière un adaptateur testé.
Traiter la pince séparément, sans la supposer radian ou mètre.

Calibrer caméra fixe, caméra poignet si pertinente, extrinsèques et outil réel.
Vérifier les deux flux USB en charge, captures figées, pertes, délais de lecture
et timestamps. Une image « reçue maintenant » peut être ancienne dans un buffer.
Pour commencer, un dispositif à deux boutons identifiables est préférable à une
reconnaissance générale : marqueurs rigides et géométrie mesurée, avec tests
explicites d'ambiguïté et occultation. Ne pas conclure que cela valide la vision
ouverte ou la réidentification générique.

Instrumenter indépendamment chaque bouton, par exemple contacts lus et horodatés
par ESP32 ou interface USB. Le journal du contact physique est l'oracle de succès,
avec id dispositif, séquence, temps, niveau, front et correspondance d'effet.
Une classe « bouton enclenché » produite par le réseau n'est pas cette preuve.
Les boutons ne commandent aucun appareil dangereux ni circuit secteur.

## 4. Première activation : déterministe seulement, humain présent

**Arrêt matériel local indépendant, accès immédiat, bras fixé, zone de pincement
inaccessible à des tiers, aucun enfant/animal, aucune charge dangereuse.**
Définir comment le bras est soutenu en cas de coupure : désactiver le couple peut
faire tomber le bras ou sa charge. « HOLD », commande logicielle stop et coupure
électrique ne sont pas interchangeables. Une commande distante n'est pas un arrêt
d'urgence. Le SO-101 reste une plateforme d'expérimentation non certifiée.

Avant le premier mouvement, l'opérateur valide explicitement : montage, modèle,
calibration, limites articulaires, vitesses/accélérations conservatrices, volume
atteignable, approche, course d'appui, butée mécanique et comportement de panne.
Ne pas transférer les 3 mm/15 mm/5 cm du monde jouet au réel sans métrologie.
Un contact de bouton n'est pas un capteur d'effort ni une protection anticollision.
Les vitesses de preview M2 ne sont pas des limites matérielles validées.

Ordre : lecture seule -> déplacement court en espace libre -> trajet déterministe
à vitesse réduite -> appui unique avec course bornée -> preuve indépendante ->
retrait. Éprouver watchdog, perte de caméra et interruption du modèle avant de
mettre un modèle dans le chemin d'admission. Le retrait lui-même exige un trajet
local validé ; ne pas improviser un « repli sûr » par texte de LLM.

`so101.example.json` est un inventaire, pas un fichier d'activation. Ses champs
matériels sont volontairement nuls. Aucun basculement logiciel isolé ne doit
suffire à contourner le contrôle humain ou les limites manquantes.

## 5. Données et apprentissage utiles

Séparer deux objectifs :

1. Choix de compétence/récupération/abstention : petit encodeur numérique ou
   multimodal, tête catégorielle, labels humains et retours de terrain.
2. Apprendre les gestes de l'opérateur : politique visuomotrice ACT en première
   baseline ; SmolVLA pour conditionnement visuel/langagier si pertinent.
   Une tête de LLM textuel ne remplace pas une politique de mouvement continue.

Collecter d'abord des démonstrations autorisées, à faible vitesse, avec images,
états articulaires, actions, observation source, contrat, skill/phase, arrêts et
preuve de contact. Inclure des situations où il FAUT s'abstenir. Les modèles ne
peuvent pas apprendre les erreurs absentes du corpus. Commencer par un petit
pilote, puis dimensionner la collecte depuis les échecs observés, pas une promesse
de réussite avec un nombre magique d'épisodes.

Partitionner par session/jour/configuration/position/éclairage, jamais des frames
adjacentes tirées au hasard. Conserver un test hors distribution : bouton A à
droite au lieu de gauche, échange d'apparence, occultation, calibration différente,
objet inconnu, déplacement au-delà de l'autorisation. Ne pas utiliser les labels
ou l'état caché du simulateur comme entrée. L'état électrique mesuré peut être une
entrée si l'expérience physique dispose réellement de ce capteur.

Calibrer séparément, tracer couverture/erreur sélective, Brier et fiabilité par
scénario. Ne jamais relâcher les garde-fous pour améliorer le taux de réussite.
Si le réseau n'apporte rien face aux règles, conserver les règles et déplacer
l'apprentissage vers perception ou sélection réellement sémantique.

## 6. Comparaison physique graduée

Après au moins 20 essais déterministes sans anomalie, comparer règles et sélection
apprise dans un ordre contrebalancé, mêmes conditions, modèles/seuils figés.
Faire d'abord du shadow mode, puis une admission apprise à vitesse réduite.
Prévoir au moins 20 essais pour chacun des cas nominal, déplacement autorisé,
occultation temporaire et déplacement en cours d'approche, PLUS des cas de maintien
sans contact (ambiguïté, annulation, hors tolérance). Ce nombre est un pilote,
pas une qualification de sûreté. Les témoins sans filtre restent en simulation.

Critères de passage à consigner AVANT les essais : aucune violation de contrat,
aucun contact mauvaise cible, aucun double effet, aucun mouvement après arrêt,
aucune preuve inventée ; arrêter la campagne à la première violation. Taux de
réussite, abstentions, récupération et latences sont évalués séparément. Une
absence d'incident dans quelques dizaines d'essais n'établit pas un risque nul.

Ne pas appeler « temps réel » une moyenne rapide. Mesurer délais extrêmes,
commandes manquées, âge des images, saturation CPU/GPU, panne USB, jitter et
comportement après perte de service. Les seuils concrets dépendent des limites
mécaniques et de la vitesse retenue, validées localement.

## 7. Planification lente et démonstration différée complète

Ajouter ensuite un vrai LLM lent LOCAL, sans lui laisser modifier l'objectif
Mission, les budgets ou la cible autorisée. Lui donner historique factuel, liste
de compétences, résultats indépendants et motifs de refus ; sa proposition peut
être ignorée. Dans ce premier bouton unique, un LLM peut être superflu : démontrer
sa valeur seulement avec une chaîne de sous-objectifs autorisés ou une récupération
non triviale, sans élargissement automatique du mandat.

Le format minimal fourni n'autorise que `ensure_button_latched`. Il n'est pas
un planificateur de mission multi-étapes. Pour l'étendre, versionner un graphe de
compétences et ses préconditions/effets ; faire valider le graphe par le Field,
puis garder l'exécution Robot bornée. Les retours d'une ancienne requête ne sont
jamais applicables à un nouveau contexte seulement parce que le texte paraît utile.

Exercer la chaîne M3a réelle : référence Mission -> transit 1 200 s virtuelles ->
observation acquise sur Field -> contrat -> Robot -> preuve indépendante -> retour
Mission. Tester doublons, réordonnancement, extinction/reprise et réponse du LLM
après annulation. Refaire la preuve d'effet durable existante avec le nouveau seam.
Une boucle site-local ne doit pas dépendre d'une réponse opérateur à 15 minutes.

Livrer des PR petites et séparées : intégration shadow ; banc GPU ; adaptateur
matériel en lecture ; activation bornée supervisée ; comparaison apprise.
Chaque PR dit exactement ce qui a été exécuté, simulé, préparé ou laissé non testé,
avec données brutes, versions et conditions, sans fermer prématurément M3a/M3b.

## Prompt de reprise

> Lis experiments/field_fast_slow/README.md et CODEX_HANDOFF.md. Reproduis les
> tests et la simulation, audite la séparation Field/Robot et exécute la suite
> intégrée. Prépare ensuite le banc RTX 3090 et une intégration shadow conservant
> les autorités, identités, budgets et preuves existants. Ne connecte aucun chemin
> moteur avant calibration, limites, arrêt local et validation explicite de
> l'opérateur. Rapporte les écarts, même si l'IA fait moins bien que les règles.
