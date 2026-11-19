# Security

## Reporting a problem

If you find a security issue (for example, a way the bot could leak credentials or send unintended orders), please **don't open a public issue**. Use GitHub's **Report a vulnerability** button under the repository's **Security** tab, so it can be fixed before it's public.

## Keeping your own setup safe

- Your MT5 login and Telegram token live in `.env`, which is git-ignored. Never commit it.
- Use a **demo account** until you've tested for weeks.
- If your broker allows it, use credentials without withdrawal rights.
- The Telegram bot only obeys your own chat ID. Keep your bot token private.
