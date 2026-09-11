# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Babel](https://babeljs.io/) (or [oxc](https://oxc.rs) when used in [rolldown-vite](https://vite.dev/guide/rolldown)) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```

## Backend provider configuration

The OmniDimension integration is backend-only. Configure these environment
variables in the backend runtime when the provider is enabled:

```text
OMNIDIMENSION_API_KEY=your-omnidimension-api-key
OMNIDIMENSION_BASE_URL=https://omnidim.io/api/v1
OMNIDIMENSION_TIMEOUT_SECONDS=15
```

Never place the API key in frontend code, browser storage, API responses, or
committed files.

Internal customer billing uses a prepaid INR wallet. Calls are charged at
`CALL_PRICE_INR` (default `8.00`) based on actual duration in seconds, with
half-up rounding to two decimal places. `MINIMUM_CALL_BALANCE_INR` defaults to
one minute of balance and prevents empty-wallet dispatches. Wallets start at
zero; no signup credit is granted. Payment gateways and public top-ups are not
implemented.

Phone inventory synchronization uses OmniDimension's authenticated
`GET /phone_number/list` endpoint. Provider inventory is stored without a
tenant assignment and is only returned after an internal assignment operation.

Post-call results are accepted at `POST /api/v1/webhooks/omnidimension/post-call`.
Configure this URL in the OmniDimension agent's Post-Call Webhook settings. The
receiver correlates provider metadata `local_call_id` (or provider call ID),
updates an existing tenant-owned call idempotently, and never stores the raw
provider payload. OmniDimension's current webhook documentation does not define
a signature header, so no unsupported signature scheme is implemented here.

You can also install [eslint-plugin-react-x](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs['recommended-typescript'],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```
