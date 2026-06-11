# Criativo API

API Python para composição de logo em criativos de vagas e publicação automática no ClickUp.

## O que faz

1. Recebe a imagem base (gerada pelo Gemini) + logo da empresa + task_id do ClickUp
2. Sobrepõe a logo no canto superior esquerdo da imagem usando Pillow
3. Publica a imagem final como anexo + comentário na task do ClickUp

## Deploy no Railway

### 1. Suba o projeto
```bash
# Via GitHub: faça push desses arquivos em um repositório
# Depois conecte o repo no Railway
```

### 2. Configure a variável de ambiente
No painel do Railway, vá em **Variables** e adicione:
```
CLICKUP_TOKEN = pk_SEU_TOKEN_AQUI
```

### 3. O Railway detecta automaticamente
- Lê o `railway.toml` e sobe com `uvicorn`
- A URL pública fica disponível em **Settings → Domains**

---

## Como usar no n8n

Adicione um nó **HTTP Request** com:

- **Method:** POST
- **URL:** https://SUA_URL.railway.app/compor-criativo
- **Body Content Type:** Form Data (Multipart)

| Campo    | Tipo   | Valor                          |
|----------|--------|-------------------------------|
| imagem   | File   | binário da imagem do Gemini   |
| logo     | File   | binário da logo da empresa    |
| task_id  | Text   | ID da task no ClickUp         |

---

## Endpoint disponível

| Método | Rota               | Descrição                        |
|--------|--------------------|----------------------------------|
| GET    | /                  | Health check                     |
| POST   | /compor-criativo   | Compõe logo + publica no ClickUp |

## Resposta de sucesso

```json
{
  "sucesso": true,
  "task_id": "abc123",
  "anexo_url": "https://attachments.clickup.com/...",
  "comentario_status": 200
}
```

## Ajustar posição da logo

No `main.py`, altere os valores em `compor_logo_na_imagem()`:
```python
logo_max_w, logo_max_h = 260, 90   # tamanho máximo da logo
pos_x, pos_y = 45, 45              # posição em pixels (x, y)
```
