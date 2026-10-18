# Onco du matin

Podcast quotidien privé : fiche de cours d'oncologie et veille, généré chaque matin de semaine.

- La tâche planifiée Claude écrit la fiche complète dans `notes/AAAA-MM-JJ.md` et le script oral dans `episodes/AAAA-MM-JJ.md`, puis les pousse sur `main`.
- L'Action `Episode` le transforme en MP3 (OpenAI `gpt-4o-mini-tts`), le publie en release et met à jour le flux.
- Chaque fiche est publiée en page web, liée dans les notes de l'épisode.
- Flux : https://stalh.github.io/onco-du-matin/feed.xml

Format d'un épisode :

```
---
title: Titre affiché dans l'app
date: 2026-10-19
summary: Deux ou trois lignes de résumé.
---
Paragraphes du script, séparés par une ligne vide.

[section]   silence court entre deux parties
[pause]     silence de réflexion après une question
```

Réglages (voix, ton, nombre d'épisodes gardés) : `podcast.json`.
Relancer à la main : Actions → Episode → Run workflow.
