# Bitcoin Gift Wallet — Paper Wallet Generator

## Project Overview

A self-contained Bitcoin paper wallet website that generates gift-ready Bitcoin bills as printable images. Users select SegWit or Taproot, and the app generates a private key, public address, QR codes, and overlays them on a bill template PNG. Includes companion websites for recipients to sweep funds and for givers to recover funds via backup key.

## Architecture

**Dual-mode static site**: all cryptography and bill generation runs client-side in JavaScript (zero external dependencies). The Python server is only needed for regtest mode (local Bitcoin Core testing). The same HTML files serve both modes — server detection via `/api/health` probe determines which features to enable.

```
GitHub Pages (static):     All JS crypto → mempool.space API → no regtest
Local Python server:       All JS crypto → regtest via server API (bitcoin-cli bridge)
```

### JavaScript Modules (client-side, zero dependencies)

- **`bitcoin_crypto.js`** (~2,200 lines) — Full port of `bitcoin_crypto.py` to JavaScript. Pure JS secp256k1 (BigInt arithmetic), SHA-256, RIPEMD-160, HMAC-SHA256, bech32/bech32m encoding (BIP173/BIP350), Base58/WIF, SegWit P2WPKH and Taproot P2TR address generation, ECDSA signing (RFC 6979), Schnorr signing (BIP340), sighash computation, and transaction construction (SegWit v0, Taproot key path, Taproot script path, multi-input sweep variants). Exports via `window.BitcoinCrypto`.
- **`qr_generator.js`** (~600 lines) — Full port of `qr_generator.py`. GF(256) arithmetic, Reed-Solomon encoding, QR versions 1-10, alphanumeric + byte modes, 8-mask evaluation. Exports via `window.QRGenerator`.
- **`bill_generator.js`** (~250 lines) — HTML5 Canvas-based bill generation (replaces Python Pillow). Loads `bill_template.png`, overlays QR codes, address text, private key text, banner, timestamp, and "(tweaked)" labels. Uses `ctx.measureText()` for font fitting. Exports via `window.BillGenerator`.
- **`test_bitcoin_crypto.html`** (~1,050 lines) — In-browser JS test suite with 120 tests covering all crypto primitives, address generation, signing, transaction construction, QR codes, bill generation, and cross-validation with Python (10 fixed keys produce identical results).

### Python Backend (server mode only)

- **`bitcoin_crypto.py`** — Core cryptography module (Python reference implementation). Pure Python secp256k1, bech32/bech32m encoding (BIP173/BIP350), SegWit P2WPKH and Taproot P2TR address generation, WIF encoding/decoding, ECDSA signing (RFC 6979), Schnorr signing (BIP340), and transaction construction.
- **`qr_generator.py`** — Pure Python QR code generation (reference implementation).
- **`bill_generator.py`** — Python Pillow-based bill generation (reference implementation). Uses Liberation Sans Narrow font for Taproot addresses.
- **`server.py`** — HTTP server with API endpoints for regtest operations (bitcoin-cli bridge), UTXO lookup, broadcast, and health check. The server is **not required** for mainnet/testnet4 — those modes work entirely client-side.

### HTML Pages (dual-mode)

- **`index.html`** — Generator frontend. All wallet generation and bill rendering happens client-side via JS. Server only needed for regtest network option.
- **`sweep.html`** — Recipient sweep page. 3-step flow: enter WIF → verify address & check balance → sweep to destination. All signing happens in the browser. UTXOs fetched from mempool.space (mainnet/testnet4) or server API (regtest).
- **`recover.html`** — Backup recovery page. Script-path spend using backup key + internal pubkey. Same dual-mode UTXO/broadcast as sweep.
- **`faucet.html`** — Regtest faucet page. Shows "Server Required" message when no server detected.
- **`bill_template.png`** — The bill background image (1843×784 pixels).

### Test Files

- **`test_bitcoin.py`** — Python crypto unit tests (54 tests).
- **`test_regtest_spending.py`** — On-chain spending tests (9 tests, requires Bitcoin Core).
- **`test_e2e_api.py`** — End-to-end API tests (8 tests, requires Bitcoin Core).
- **`test_ui_playwright.py`** — Playwright browser UI test (regtest, headless CI/CD).
- **`test_ui_playwright_testnet4.py`** — Playwright browser UI test for testnet4.
- **`test_ui_chained.md`** — Claude Code prompt file for interactive UI testing.
- **`test_ui_chained_testnet4.md`** — Claude Code prompt file for interactive testnet4 UI testing.

## Running

### Static mode (no server needed — GitHub Pages compatible)

```bash
# Serve with any static file server:
python3 -m http.server 8080
# Or deploy to GitHub Pages — just push to main branch
```

In static mode, all cryptography runs client-side in JavaScript. The site works with mainnet and testnet4 (UTXOs and broadcast via mempool.space API). Regtest is not available without the Python server.

### Server mode (with Python backend)

```bash
pip install -r requirements.txt  # Pillow + Playwright
playwright install chromium      # Download browser binary (for UI tests)
./run.sh                          # Starts on http://localhost:8080
# or: python3 server.py 8080
```

### Regtest mode (local Bitcoin Core testing)

```bash
./run.sh --regtest                # Starts bitcoind + server on http://localhost:8080
# or: python3 server.py 8080 --regtest
```

Regtest mode starts a managed `bitcoind` process, creates a wallet, mines 101 blocks for maturity, and enables the faucet page. Transactions broadcast on regtest are auto-confirmed (1 block mined after each broadcast). Requires Bitcoin Core (`brew install bitcoin`).

### GitHub Pages Deployment

The site can be deployed directly to GitHub Pages:
1. Push to `main` branch (or configure GitHub Pages to serve from any branch)
2. Visit `https://<username>.github.io/bitcoin-gift-wallet/`
3. Only mainnet and testnet4 are available (no regtest without Python server)
4. All crypto, QR generation, and bill rendering happen in the browser

### Server Detection

All HTML pages auto-detect the Python server on load:
```javascript
fetch('/api/health', { signal: AbortSignal.timeout(2000) })
```
- **Server detected**: regtest option shown in network dropdown, faucet link visible
- **No server (static)**: regtest hidden, faucet shows "Server Required" message

### Claude Code Preview

The project includes `.claude/launch.json` with two server configurations for Claude Code's preview feature:
- **`bitcoin-gift-wallet`** — Standard mode (mainnet/testnet4 via mempool.space)
- **`bitcoin-gift-wallet-regtest`** — Regtest mode (local bitcoind)

Use `preview_start` with either name to launch the server with live preview.

### Pages

- Generator: `http://localhost:8080/`
- Sweep: `http://localhost:8080/sweep.html`
- Recover: `http://localhost:8080/recover.html`
- Faucet (regtest only): `http://localhost:8080/faucet.html`
- JS Test Suite: `http://localhost:8080/test_bitcoin_crypto.html`

## Bill Private Key Strategy

The private key printed on the bill depends on the address type:

- **SegWit** — Standard private key WIF. Any wallet can import it.
- **Taproot (no backup)** — Internal (untweaked) private key WIF. Standard BIP86-compatible wallets (Sparrow, BlueWallet, Bitcoin Core) can import and derive the correct address.
- **Taproot (with backup)** — **Tweaked** private key WIF + "(tweaked)" label on the bill. The tweak incorporates the script tree, so the tweaked key derives the correct address directly. Recipients use the sweep page (`sweep.html`) to spend. The giver keeps the backup key for script-path recovery via `recover.html`.

## API Endpoints

### Generator
- `POST /api/generate` — Generate a new wallet + bill image
- `POST /api/download` — Download bill as PNG

### Sweep (recipient)
- `POST /api/sweep/derive` — Derive address from WIF (for verification)
- `POST /api/sweep` — Build, sign, broadcast sweep transaction

### Recovery (giver backup)
- `POST /api/recover/derive` — Reconstruct address from backup key + internal pubkey
- `POST /api/recover` — Build, sign, broadcast script-path recovery transaction

### Faucet (regtest only)
- `POST /api/faucet` — Fund an address with test BTC (amount in BTC, default 1.0)
- `POST /api/mine` — Mine 1–100 blocks on regtest

### Shared
- `GET /api/health` — Server health check. Returns `{"status": "ok", "regtest": true/false}`. Used by HTML pages to detect server presence.
- `POST /api/utxos` — Look up UTXOs for an address (regtest via bitcoin-cli, mainnet/testnet4 via mempool.space)
- `POST /api/broadcast` — Broadcast a raw transaction. Accepts `{raw_hex, network}`, returns `{txid}`. Used by sweep/recover pages in regtest mode.

## Tests

### JavaScript crypto tests (120 tests, runs in browser)
```bash
# Open in any browser:
open test_bitcoin_crypto.html
# Or via HTTP server:
python3 -m http.server 8080  # then visit http://localhost:8080/test_bitcoin_crypto.html
```
120 tests covering: byte helpers, SHA-256, RIPEMD-160, HMAC-SHA256, hash160, tagged hash, secp256k1 point arithmetic, bech32/bech32m, Base58/WIF, SegWit address generation, Taproot address generation, Taproot backup keys, tweaked address derivation, edge cases, ECDSA signing, Schnorr signing (BIP340), transaction construction (SegWit, Taproot key path, Taproot script path, multi-input), QR code generation (with Python cross-validation), bill generation, script path verification, key/script path compatibility, consistency (200 unique addresses), testnet4 comprehensive, and **cross-validation with Python** (10 fixed private keys produce identical SegWit addresses, Taproot addresses, and WIF encodings in both Python and JS).

### Python crypto unit tests (54 tests, no dependencies beyond the project)
```bash
python3 test_bitcoin.py
```
These verify address generation (mainnet, regtest, testnet4), key derivation, bech32/bech32m encoding, taproot tweaking, WIF encoding/decoding roundtrips (all 3 networks), tweaked key address derivation, Schnorr signatures (including BIP340 test vectors), and ECDSA signatures.

### Regtest spending tests (requires Bitcoin Core: `brew install bitcoin`)
```bash
python3 test_regtest_spending.py
```
9 tests that prove generated addresses are actually spendable on-chain:
1. SegWit P2WPKH spending
2. Taproot key path spending (no script tree)
3. Taproot key path spending (with script tree / backup key)
4. Taproot script path spending (backup key)
5. Multiple SegWit addresses (batch)
6. Multiple Taproot addresses (batch)
7. Recipient spend (no backup) — simulates standard wallet import from untweaked WIF
8. Recipient sweep (with backup) — simulates sweep page flow from tweaked WIF
9. Script path recovery — simulates giver using backup key for script-path spend

Each test: generates an address → funds it on regtest → constructs + signs a spending tx in pure Python → broadcasts → mines a block → verifies the tx is confirmed with the exact expected output amount (UTXO check).

### E2E API tests (requires Bitcoin Core)
```bash
python3 test_e2e_api.py
```
8 tests that exercise the full HTTP API workflow on regtest: generate wallet → fund via faucet → sweep or recover via API → verify on-chain. Covers SegWit sweep, Taproot sweep (no backup), Taproot sweep (with backup/tweaked), Taproot recovery (script-path), multi-UTXO sweep, repeated multi-UTXO sweep (all 3 address types × 2 rounds of fund-then-sweep), repeated multi-UTXO recovery (script-path × 2 rounds of fund-then-recover), and a chained test (faucet → sweep → recovery across 3 Taproot+backup addresses).

### Playwright UI tests (requires Bitcoin Core + Playwright)
```bash
pip install playwright && playwright install chromium
python3 test_ui_playwright.py              # headless (CI/CD)
python3 test_ui_playwright.py --headed     # visible browser (debugging)
```
Browser-driven UI test that exercises the full chained flow through actual web pages: Generator → Faucet → Sweep → Recovery. Starts its own regtest server subprocess, launches headless Chromium, drives the UI with real clicks and form fills, and verifies transaction confirmations and fee chain math. Screenshots saved to `test-screenshots/` at each checkpoint.

### Testnet4 Playwright UI test (requires Playwright + pre-funded testnet4 address)
```bash
python3 test_ui_playwright_testnet4.py --wif "cXXX..." --address "tb1q..."
python3 test_ui_playwright_testnet4.py --wif "cXXX..." --address "tb1p..." --headed

# Or via environment variables (set in .claude/settings.local.json):
export TESTNET4_WIF="cXXX..."
export TESTNET4_ADDRESS="tb1q..."
python3 test_ui_playwright_testnet4.py
```
Testnet4 browser-driven test: sweeps a pre-funded testnet4 address to a freshly generated address, then immediately returns funds (spending the unconfirmed output) back to the original address. No confirmation waits — completes as soon as transactions are accepted by the mempool, with explorer links printed for traceability. Supports both SegWit (`tb1q...`) and Taproot (`tb1p...`) funded addresses — the address type is auto-detected from the prefix. Funds go back to the original address so the test wallet isn't drained. CI/CD-ready via pipeline secrets (`--wif` / `--address` CLI args) or `TESTNET4_WIF` / `TESTNET4_ADDRESS` env vars.

## Current State

**All tests pass: 120/120 JS crypto tests + 54/54 Python unit tests + 9/9 regtest spending tests + 8/8 E2E API tests + 1/1 Playwright UI test.**

The site works as a fully static site (GitHub Pages compatible) with all crypto, QR generation, and bill rendering happening client-side in JavaScript. The Python server is only needed for regtest mode.

Funding uses `createrawtransaction` + `fundrawtransaction` + `signrawtransactionwithwallet` to avoid `sendtoaddress`, which hangs indefinitely for bech32m (Taproot) addresses on Bitcoin Core v30.2.0.

Post-spend verification (`confirm_tx`) checks both that the spending tx has >=1 confirmation and that the destination output contains the exact expected satoshi amount.

## Platform Notes (macOS)

Three issues were encountered running the regtest tests on macOS:

1. **`rpcport` config section** — Bitcoin Core v28+ requires `rpcport` to be in the `[regtest]` section of `bitcoin.conf`, not the global section.
2. **`ulimit -n unlimited`** — macOS maps this to `RLIM_INFINITY` (-1), which causes bitcoind to refuse to start ("Not enough file descriptors available. -1 available"). The test uses `resource.setrlimit` to set a concrete limit (4096) before launching bitcoind.
3. **Subprocess pipe buffer deadlock** — bitcoind started with `stdout=subprocess.PIPE` will block after ~64KB of log output fills the OS pipe buffer (since nobody reads from it), causing all RPC calls to hang. Fixed by using `subprocess.DEVNULL`.

## Key Design Decisions

- **No external crypto libraries.** All secp256k1 math, hashing, signing is pure Python (reference implementation) and pure JavaScript (client-side). Both implementations are hand-rolled from scratch for auditability. The JS port uses BigInt for secp256k1 arithmetic and synchronous SHA-256 (not WebCrypto) for deterministic operation.
- **Taproot addresses support an optional backup key** via a script tree with a single leaf containing `<backup_pubkey> OP_CHECKSIG`. The primary spend path is the key path (Schnorr).
- **Conditional bill WIF.** No-backup Taproot prints the untweaked key (wallet-compatible). Backup Taproot prints the tweaked key with a "(tweaked)" visual label (requires sweep page).
- **Address generation uses CSPRNG entropy**: Python uses `secrets.token_bytes(32)`, JavaScript uses `crypto.getRandomValues(new Uint8Array(32))`. Both use rejection sampling to ensure keys are in `[1, N-1]`. See "Cryptographic Entropy" section below.
- **Font handling**: Taproot addresses (62 chars) use Liberation Sans Narrow to achieve the same visual font size as SegWit addresses (42 chars) on the printed bill.
- **Fee estimation**: Sweep/recover pages show live total fee based on fee rate and estimated vsize. Taproot key-path: 11 + N*58 + 43 per output. Taproot script-path: 11 + N*107 + 43. SegWit: 11 + N*69 + 31.

## Cryptographic Entropy

### How Private Keys Are Generated

`generatePrivateKey()` in `bitcoin_crypto.js` (and `generate_private_key()` in `bitcoin_crypto.py`) obtains 256 bits of entropy via CSPRNG, then uses **rejection sampling** — looping until the random integer falls in the valid secp256k1 range `[1, N-1]`. The probability of rejection is ~3.7 × 10⁻³⁹ per attempt, so in practice the loop always exits on the first iteration.

- **JavaScript**: `crypto.getRandomValues(new Uint8Array(32))` — Web Crypto API, backed by the same OS CSPRNG
- **Python**: `secrets.token_bytes(32)` — backed by `os.urandom()` → OS kernel CSPRNG

Both are called by `generateSegwitAddress()` / `generateTaprootAddress()` (twice for Taproot+backup: once for the internal key, once for the backup key).

### Underlying OS Entropy Source

The call chain is: `secrets.token_bytes()` → `os.urandom()` → OS kernel CSPRNG:

| Platform | System Call | Underlying RNG |
|----------|-----------|----------------|
| macOS 10.12+ | `getentropy(3)` | Fortuna CSPRNG (SHA-256), seeded by Secure Enclave hardware RNG |
| Linux 3.17+ | `getrandom(2)` (blocking) | Kernel CSPRNG (ChaCha20-based since Linux 4.8) |
| Windows | `BCryptGenRandom()` | Windows CNG CSPRNG |

On Linux, `getrandom(2)` blocks only during early boot until the kernel CSPRNG is seeded, then never blocks again. On macOS, the Secure Enclave hardware RNG seeds the CSPRNG at boot.

### Signing Nonce Safety

ECDSA (SegWit) uses **RFC 6979 deterministic k** — no runtime randomness needed for nonces. Schnorr (Taproot) uses **BIP340 nonce derivation** with optional `aux_rand` from `secrets.token_bytes(32)` — even if `aux_rand` were weak, the nonce is still derived deterministically from the private key and message, preventing catastrophic nonce-reuse attacks.

### Comparison With Other Bitcoin Wallets

| Property | This Project | Bitcoin Core | Electrum | BlueWallet | Sparrow |
|----------|-------------|-------------|----------|------------|---------|
| Primary entropy | OS CSPRNG (`secrets.token_bytes`) | OS + RDRAND + RDSEED + perf counters + internal state | OS CSPRNG (`os.urandom`) | OS CSPRNG (`SecureRandom` / `SecRandomCopyBytes`) | OS CSPRNG (`/dev/random`) |
| Entropy bits per key | 256 | 256 (mixed sources) | 132 (seed phrase) | 128–256 (BIP39) | 128–256 (BIP39) |
| Multiple entropy sources | No (OS only) | Yes (5+ sources mixed via SHA-512) | No (OS only) | No (OS only) | No (OS only) |
| User-supplied entropy | No | No | No | Yes (dice/coins) | No |
| Rejection sampling | Yes | Yes | Yes (modular reduction) | Yes (BIP39) | Yes (BIP39) |

**Verdict:** The entropy source is functionally identical to Electrum (both use `os.urandom()` under the hood) and on par with BlueWallet and Sparrow. Bitcoin Core is the only wallet that goes further, mixing 5+ independent entropy sources as defense-in-depth — designed for a long-running node, not a one-shot generator. For a paper wallet generator on macOS (where Secure Enclave hardware RNG seeds the kernel CSPRNG), single-source OS entropy is a sound engineering choice.

## Network Support

Three networks are supported, each with distinct address prefixes:

| Network | SegWit Prefix | Taproot Prefix | WIF Prefix | Mempool API |
|---------|--------------|----------------|------------|-------------|
| Mainnet | `bc1q...` | `bc1p...` | `0x80` (K/L) | `mempool.space/api` |
| Testnet4 | `tb1q...` | `tb1p...` | `0xef` (c) | `mempool.space/testnet4/api` |
| Regtest | `bcrt1q...` | `bcrt1p...` | `0xef` (c) | N/A (bitcoin-cli) |

**Note:** Testnet4 and regtest share WIF prefix `0xef`, so the network must be specified by the user (WIF alone cannot distinguish them).

Both `bitcoin_crypto.py` and `bitcoin_crypto.js` functions use a `network` parameter: `"mainnet"`, `"testnet4"`, or `"regtest"`. The `_networkHrp()` / `_network_hrp()` helper maps these to bech32 HRPs: `"bc"`, `"tb"`, `"bcrt"`.

- The test RPC port is 18443 with rpcuser=test, rpcpassword=test.

## Regtest Server Architecture

When started with `--regtest`, the server manages a full `bitcoind` lifecycle:

1. **`RegtestNode` class** (in `server.py`) — Creates a temp datadir, writes `bitcoin.conf`, starts `bitcoind`, creates a wallet, mines 101 blocks for coinbase maturity.
2. **Auto-mine** — `_broadcast_regtest()` automatically mines 1 block after broadcasting, so transactions confirm immediately. Sweep/recover pages show "Transaction Confirmed" instead of "Transaction Broadcast" on regtest.
3. **Faucet** — `POST /api/faucet` calls `sendtoaddress` + mines 1 block. The faucet page maintains an in-session history of funded addresses.
4. **Cleanup** — `RegtestNode.stop()` shuts down `bitcoind` and removes the temp datadir on server exit.

## UI Notes

- All pages include navigation links (Generator, Sweep, Backup Recovery, Regtest Faucet).
- Faucet link is hidden when no server detected (static/GitHub Pages mode).
- Regtest option is removed from network dropdowns when no server detected.
- All transaction signing happens client-side in the browser (security notice shown on sweep/recover pages).
- Sweep and recover pages use dual-mode UTXO lookup: `mempool.space` API for mainnet/testnet4, server `/api/utxos` for regtest.
- Sweep and recover pages use dual-mode broadcast: `mempool.space` API for mainnet/testnet4, server `/api/broadcast` for regtest.
- Sweep and recover pages use `AbortController` with a 60-second timeout on fetch requests to prevent buttons getting stuck in loading state if the server hangs.
- Sweep and recover pages use `finally` blocks to guarantee button state restoration on all code paths (success, error, timeout).
- Bill template image (`bill_template.png`) is pre-loaded on page load; the Generate button is enabled only after the template loads successfully.
