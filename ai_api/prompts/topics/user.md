
Process the following headlines. For each headline, you are given the original text on one line and its entity list on the next line. Treat each headline independently. Indices into the entity list are **0-based**. Return a single JSON array of results, where each element matches the schema:

```json
{
  "text": "<original headline exactly>",
  "topics": [
    { "text": "<exact substring from headline>", "entities": [<indices>] }
  ]
}
```

Return **only** the JSON array.
