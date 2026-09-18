# Field fast/slow — SO-101 / RTX 3090

**Expérience exploratoire du 18 septembre 2026. Aucun accès aux moteurs.**
Base examinée : `26783d9cae483dd119179135accdb48829fc4fc6`.
Lire [CODEX_HANDOFF.md](CODEX_HANDOFF.md) avant toute intégration ou expérimentation physique.

## Décision d'architecture

Oui à un sélecteur appris, local, à sorties bornées, complété par un planificateur
lent. Non à un LLM dans l'asservissement des moteurs. Le qualificatif « type Jev »
désigne ici une interface de décision probabiliste sans génération de texte,
**pas une reproduction de l'architecture propriétaire de Jev ou de RLCD**.

```text
Mission : intention, cohérence distante, liaison pouvant dépasser 15 min
    -> Field : admission/coordination + propositions d'un LLM local asynchrone
        -> Robot : sélecteur de compétences expérimental, éventuellement appris
            -> compétence bornée : transitions, IK/trajectoire, vérifications déterministes
                -> driver/servos ; arrêt et sûreté locaux indépendants du modèle
```

Les processus Field, inférence et Robot peuvent partager le PC terrain, mais leurs
**autorités restent distinctes**. Une sortie neuronale ne crée ni autorisation,
ni nouvelle identité, ni budget d'effet. L'appel distant n'est jamais nécessaire
pour terminer une compétence déjà autorisée et bornée, ou pour l'arrêter localement.

Le dépôt possède déjà la séparation pertinente : `M3aFieldService`,
`M3aRobotService`, `decide_two_button`, `derive_spatial_press_command`, budget
durable et journal indépendant du dispositif. Cette expérience est un laboratoire
**adjacent**, pas une modification de ces services. Ses labels
`HOLD/APPROACH/PRESS/VERIFY` ne sont pas les actions du protocole M3a.

## Ce qui a réellement été exécuté

Un MLP initialisé aléatoirement, `8 -> 48 -> 48 -> 4`, soit **2 980 paramètres**,
apprend une politique explicite sur 16 000 états synthétiques. La température
est choisie sur 4 000 autres états ; 4 000 états distincts servent au test.
Les labels viennent de règles, pas de démonstrations humaines ou d'un LLM.

Le monde est un point de contact 2D, avec primitives mathématiques approche/appui,
et un dispositif simulé qui décide indépendamment quel bouton a été touché.
Ce n'est **ni une dynamique SO-101, ni une simulation de caméras, ni une preuve de
cinématique, de collision ou de contrôle de force**.

Huit scénarios : nominal, cible déplacée, dépassement de tolérance, identité
ambiguë, occultation temporaire, annulation, bouton déjà enclenché, déplacement
en cours d'approche. Chaque politique reçoit les mêmes conditions et graines.
Vingt épisodes par scénario, cinq politiques, deux graines d'entraînement :
**1 600 épisodes exécutés**. La variante « sélecteur + compétence » a été ajoutée
après un pilote ayant révélé le blocage du contrôleur neuronal de phases ; la
seconde graine est une vérification exploratoire, pas une étude préenregistrée.

| Politique | Objectifs atteints, graine 20260918 | Graine 20260928 | Contacts non autorisés |
|---|---:|---:|---:|
| Règles locales + filtre | 100/100 | 100/100 | 0/160 pour chaque graine |
| MLP décide chaque phase + filtre | 48/100 | 20/100 | 0/160 pour chaque graine |
| MLP admet une compétence, transitions par règles | 100/100 | 100/100 | 0/160 pour chaque graine |
| Oracle instantané dont la réponse est retardée artificiellement de 2 s | 0/100 | 0/100 | 0/160 pour chaque graine |
| Ancienne référence, sans filtre — témoin volontairement incorrect | 60/100 | 60/100 | 100/160 pour chaque graine |

Les 100 objectifs comprennent **80 appuis à réaliser + 20 états déjà satisfaits**.
Les 60 autres épisodes exigent de ne pas agir. La variante retenue effectue donc
80/80 appuis autorisés par graine, et non 100 appuis. Le témoin sans filtre est
un contrôle négatif, pas un concurrent équitable ; ne jamais l'exécuter sur le bras.

L'oracle retardé est **un délai injecté**, pas une mesure d'un LLM. Avec des états
valables 100 ms et des réponses arrivant 2 s plus tard, son absence de progression
est attendue par construction. Un vrai plan lent doit proposer une compétence,
pas une action attachée à une pose périmée.

La classification hors boucle atteint 99,10 % puis 99,225 %. Cela ne suffit pas :
le réseau qui pilote chaque phase se bloque près de la transition approche/appui.
L'admission apprise suivie de transitions déterministes évite ce défaut dans ce
monde simple. **Elle ne bat pas les règles**. Il n'y a qu'une compétence utile ici :
l'expérience n'établit donc aucun gain de compréhension sémantique ou de sélection
entre plusieurs compétences.

Sur le CPU de préparation, graine principale, inférence MLP chaude : médiane
0,026 ms, p95 0,052 ms, p99 0,212 ms, maximum observé 2,380 ms sur 2 000 appels.
Ces temps n'incluent ni perception, ni USB, ni servos, ni ordonnanceur terrain.
**Aucun temps RTX 3090 n'a été mesuré.**

Rapports complets : [evidence/report.json](evidence/report.json),
[evidence/confirmation.json](evidence/confirmation.json), journaux d'épisodes
compressés et [evidence/validation.json](evidence/validation.json).

## Reproduire sans matériel

Depuis la racine du dépôt, dans un environnement Python avec NumPy et PyTorch :

```bash
cd experiments/field_fast_slow
python -m unittest discover -s . -p 'test_*.py' -v
python experiment.py --out results/cpu
python experiment.py --seed 20260928 --out results/confirmation
```

Pour une reproduction CUDA, utiliser un environnement PyTorch CUDA validé sur
la machine puis `python experiment.py --device cuda --out results/3090`.
La simulation ne se connecte jamais au bras. Elle refuse de présenter un run CPU
comme CUDA. Les résultats sont reproductibles au sein d'un environnement fixé ;
l'identité binaire entre CPU/CUDA ou versions différentes n'est pas garantie.

Le fichier `requirements-experiment.txt` contient des plages de dépendances,
**pas un lock vérifié sur la 3090**. Le rapport enregistre les versions effectivement
utilisées. Les 47 tests locaux et la compilation Python passent ici. La suite
complète du dépôt, Ruff, Unreal, les poids Qwen et le matériel n'ont pas été exécutés.

## Fichiers préparés pour l'expérience 3090

- `core.py` : propositions immuables, vérifications de fraîcheur, identité,
  version, arrêt, contexte, préconditions et probabilités. Filtre expérimental,
  non certifié et non suffisant pour le matériel.
- `experiment.py` : entraînement MLP, calibration, comparaisons et journaux.
- `text_head.py` : recette **non exécutée** Qwen3-0.6B + tête de classification
  à quatre sorties + LoRA. Un passage avant, aucune génération de tokens.
  Révision HF exacte obligatoire, téléchargement volontaire, données sans
  labels/identifiants dans l'entrée, séparation par session, calibration et
  mesures latence/mémoire. C'est un comparateur de phases, pas le sélecteur
  multi-compétences final : changer sa taxonomie et ses données avant ce rôle.
- `slow_client.py` : client local asynchrone à une requête en vol, JSON strict,
  rejet des réponses périmées. Aucun accès aux moteurs, aucun contrat modifié.
- `so101.example.json` : inventaire des paramètres à déterminer ; matériel désactivé.

Pour préparer la comparaison Qwen, résoudre d'abord la révision réelle du modèle
et l'enregistrer. Le script refuse `main` et les SHA fictifs :

```bash
# MODEL_SHA doit être le SHA réel de Qwen/Qwen3-0.6B, vérifié par Codex.
python text_head.py --data results/cpu --out results/qwen-head \
  --model Qwen/Qwen3-0.6B --revision "$MODEL_SHA" --allow-download
```

Installer les dépendances optionnelles dans un environnement distinct ; vérifier
les signatures des API et produire un lock avant de comparer les performances.
La recette n'appelle aucun service payant, ne téléverse aucune donnée et ne
télécharge pas de poids sans `--allow-download`.

## Sources et portée de la comparaison

Sources primaires consultées le 18 septembre 2026 :

- [Annonce TypeSafe](https://typesafe.ai/blog/introducing-system-one-models-and-jev) :
  sorties probabilistes typées, RLCD revendiqué, 70–500 ms annoncées. Ce n'est pas
  une garantie temps réel dur ; leur démonstration Doom utilise un état textuel.
- [Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B) : modèle causal Apache-2.0,
  candidat de comparaison, non présenté comme le meilleur modèle actuel.
- [Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B) : candidat de planification locale,
  à mesurer séparément et sous contention GPU.
- [LeRobot ACT](https://huggingface.co/docs/lerobot/en/act) et
  [SmolVLA](https://huggingface.co/docs/lerobot/en/smolvla) : voies pertinentes pour
  apprendre des gestes depuis caméras, état robot et démonstrations. Une tête
  textuelle de classification n'est pas une politique visuomotrice.
- [LeRobot SO-101](https://huggingface.co/docs/lerobot/en/so101) : point d'entrée
  matériel ; les unités et options du driver installé doivent être vérifiées.

Tout le code ajouté est original pour cette expérience et relève de la licence
Apache-2.0 du dépôt. Aucun code tiers, poids, capture ou donnée personnelle n'est
inclus. Les données synthétiques sont régénérées localement.
