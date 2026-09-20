# Servidor Brayan Roblox v2

Reconstrução fiel do servidor AISAKA/CAELUS com correções:
- Catálogo apontando para catalog.roblox.com (antes apis.roblox.com, que retornava vazio)
- Imagens do CDN rbxcdn repassadas (antes voltava JSON vazio, imagens cinzas)
- Todo o resto igual: settings, device/initialize, account-info, batch thumbnails,
  universal-app-config, crash upload, download do APK, _logs, signalr

Branch usada pelo serviço web do Railway (projeto captivating-magic).
O código do Free Fire segue em main e nas releases.
