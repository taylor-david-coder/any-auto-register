将 Gmail Bridge 所需凭证放在本目录：

- credentials.json  (Google OAuth Desktop App 客户端凭证)
- token.json        (首次授权后生成)

默认 docker-compose 通过以下挂载读取：
- ./gmail-bridge/credentials.json -> /app/credentials.json
- ./gmail-bridge/token.json       -> /app/token.json
