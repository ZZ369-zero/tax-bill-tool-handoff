# 7501 税单工具上线说明

## 推荐架构

- GitHub 私有仓库：保存源代码、测试、部署配置和开发历史。
- GitHub Actions：每次推送后自动运行测试。
- Render Web Service：运行 FastAPI、解析 PDF、计算并生成更新税单。
- 临时文件：上传税单仅保存在 Render 临时目录，不写入 GitHub。

GitHub Pages 只能发布静态 HTML、CSS 和 JavaScript，不能运行本项目的
Python/FastAPI 服务，因此不能单独承载 PDF 解析和税单生成。

## 首次上线

1. 确认 GitHub 仓库 `ZZ369-zero/tax-bill-tool-handoff` 保持为私有仓库。
2. 将本项目推送到仓库的 `main` 分支。
3. 登录 Render，并授权 Render GitHub App 访问上述私有仓库。
4. 打开以下 Blueprint 地址：
   `https://dashboard.render.com/blueprints/new?repo=https%3A%2F%2Fgithub.com%2FZZ369-zero%2Ftax-bill-tool-handoff`
5. Render 会读取仓库根目录的 `render.yaml`。
6. 为 `APP_USERNAME` 设置网站登录用户名。
7. 为 `APP_PASSWORD` 设置不少于 16 位的随机密码。
8. 创建 Blueprint，等待 GitHub Actions 测试和 Render 部署完成。
9. 打开 Render 提供的 `https://...onrender.com` 地址并登录。

## 后续更新

1. 在任意电脑克隆同一个私有仓库。
2. 修改代码并运行：

   ```powershell
   python -m pip install -r requirements.txt
   python -m unittest discover -s tests -v
   ```

3. 提交并推送到 GitHub。
4. GitHub Actions 测试通过后，Render 自动部署新版本。

## 数据与安全规则

- 不要把客户税单、商业发票、Excel、解析 JSON 或访问令牌提交到 GitHub。
- `.gitignore` 已排除 PDF、Excel、CSV、ZIP、`.env`、`uploads/` 和 `output/`。
- 不要把 `APP_PASSWORD` 或 GitHub Token 写入代码；只在 Render 环境变量中设置。
- 当前部署把上传文件视为临时工作文件，Render 重启或重新部署后会清除。
- 若以后需要历史税单、多用户权限和审计记录，应增加加密对象存储及数据库，
  不应把 Git 仓库改造成业务数据存储。
