# Security Assessment: Bitcoin Gift Paper Wallet

## Executive Summary

This is a thorough security review of the Bitcoin Gift Paper Wallet project — a client-side Bitcoin paper wallet generator with SegWit and Taproot support. The codebase is written with a clear understanding of Bitcoin cryptography and follows many best practices. The core cryptographic implementation is sound. However, there are a number of security concerns ranging from moderate web security issues to inherent operational risks of paper wallet generation in a browser.

The findings are organized by category, with severity ratings and specific file/line references.

---

## 1. Cryptographic Security

### 1.1 Entropy and Key Generation

**Finding 1.1.1: Key generation entropy source is appropriate**
- Severity: Info (Positive)
- File: `bitcoin_crypto.js`, lines 599-608
- The `generatePrivateKey()` function uses `crypto.getRandomValues()` with rejection sampling in range `[1, N-1]`. This is the correct approach and matches industry standard practice.

**Finding 1.1.2: BigInt timing side-channel in key validation**
- Severity: Low (Theoretical)
- File: `bitcoin_crypto.js`, line 604
- The comparison `keyInt > 0n && keyInt < N` uses JavaScript BigInt comparisons, which may not be constant-time. However, this is only used during key generation on data from the CSPRNG, so the timing leak reveals nothing about the generated key to a local attacker who doesn't already have access to the CSPRNG output. Not exploitable in practice.

### 1.2 Signing Nonce Safety

**Finding 1.2.1: ECDSA uses RFC 6979 deterministic k — correct**
- Severity: Info (Positive)
- File: `bitcoin_crypto.js`, lines 1188-1207
- The `_deterministicK()` function correctly implements RFC 6979, eliminating the catastrophic risk of random nonce reuse. This is the gold standard for ECDSA nonce generation.

**Finding 1.2.2: Schnorr signing uses BIP340 nonce derivation with aux_rand — correct**
- Severity: Info (Positive)
- File: `bitcoin_crypto.js`, lines 1257-1297
- Uses `crypto.getRandomValues(auxRand)` as additional entropy in the BIP340 nonce derivation. Even if `auxRand` were zero, the nonce would still be deterministically safe (derived from the private key and message). The implementation also verifies signatures before returning them (line 1293), which is a defensive best practice.

### 1.3 Timing and Side-Channel Attacks

**Finding 1.3.1: Non-constant-time scalar multiplication (double-and-add)**
- Severity: Medium (Theoretical)
- File: `bitcoin_crypto.js`, lines 531-543
- The `pointMul()` function uses a simple double-and-add algorithm that branches on each bit of the scalar (`if (k & 1n)`). This is textbook vulnerable to timing/power analysis side channels. However, exploiting this requires either:
  - Physical access to the device during signing (power analysis)
  - Extremely precise remote timing measurements (unrealistic over a network)
  - A colocated attacker on the same machine measuring CPU cache timings
- For a paper wallet generator running in a browser, the practical risk is very low. The key is generated and used for signing once, in a single session. This is not a persistent hot wallet that signs repeatedly.
- Mitigation: For defense in depth, a Montgomery ladder or constant-time implementation could be used, but the practical benefit is minimal for this use case.

**Finding 1.3.2: Non-constant-time modular inverse**
- Severity: Low (Theoretical)
- File: `bitcoin_crypto.js`, lines 488-502
- `_extendedGcd` and `_modinv` use the extended Euclidean algorithm, which is not constant-time. Same mitigating factors as 1.3.1 apply.

**Finding 1.3.3: BigInt arithmetic in JavaScript is not guaranteed constant-time**
- Severity: Low (Theoretical)
- File: `bitcoin_crypto.js` (throughout)
- JavaScript BigInt operations are implemented by the engine (V8, SpiderMonkey, etc.) and are not designed to be constant-time. All modular arithmetic, point operations, and key manipulations may leak timing information. This is an inherent limitation of implementing cryptography in JavaScript.
- Mitigation: Accept as a trade-off of the "no external dependencies" design. The realistic threat model for a paper wallet generator does not include timing attacks.

### 1.4 Key Material Handling in Memory

**Finding 1.4.1: Private keys remain in JavaScript heap, not zeroed after use**
- Severity: Medium
- File: `index.html`, line 606; `sweep.html`, line 575
- After wallet generation, private key material is stored in the `currentWallet` object (index.html) and `state` object (sweep.html) and remains in memory for the lifetime of the page. JavaScript provides no reliable way to zero memory (Uint8Arrays can be overwritten, but BigInt values used in computations cannot be deterministically freed).
- The `state` object in sweep.html stores `privkeyBytes` indefinitely until `reset()` is called.
- Mitigation: While JavaScript's garbage collector will eventually reclaim the memory, there is no deterministic zeroing. The `reset()` function in sweep.html does reassign the state object, which is the best available approach. For the generator page, there is no equivalent cleanup. Consider adding a "Clear Keys" button and explicitly overwriting `currentWallet` and any Uint8Array key buffers with zeros on page unload.

**Finding 1.4.2: Private keys displayed in DOM via innerHTML**
- Severity: Medium
- File: `index.html`, lines 663, 674-679
- The `detailRow()` function constructs HTML strings containing private keys and injects them via `innerHTML`. While the values are hex/WIF strings (not user-controlled input), the key material is now in the DOM as text nodes, accessible to any JavaScript running on the page (including browser extensions).
- Any browser extension with content script permissions can read these DOM elements.
- Mitigation: This is somewhat inherent to the application's purpose (showing the user their keys), but the security notice should mention the risk of browser extensions. Consider showing keys only on explicit user action (click to reveal).

### 1.5 Cryptographic Implementation Correctness

**Finding 1.5.1: ECDSA low-s normalization — correct**
- Severity: Info (Positive)
- File: `bitcoin_crypto.js`, line 1223
- Correctly normalizes `s` values per BIP62 to prevent malleability.

**Finding 1.5.2: WIF checksum verification is not constant-time**
- Severity: Info
- File: `bitcoin_crypto.js`, lines 841-845
- The checksum comparison in `wifToPrivateKey()` uses a byte-by-byte comparison that short-circuits. This is fine because the checksum is over public data (the WIF string itself), not secret material.

---

## 2. Web Security

### 2.1 Cross-Site Scripting (XSS)

**Finding 2.1.1: innerHTML usage with transaction/wallet data**
- Severity: Medium
- Files:
  - `index.html`, line 663 (`details.innerHTML = html`)
  - `sweep.html`, line 835 (`resultBox.innerHTML = html`)
  - `recover.html`, line 864 (`resultBox.innerHTML = html`)
  - `faucet.html`, lines 447-455 (`list.innerHTML = ...`)
- The `detailRow()` function in index.html injects `label` and `value` parameters directly into HTML via template literals. The `label` values are hardcoded strings (safe), and `value` comes from the crypto functions (hex/bech32 strings that cannot contain HTML metacharacters `<>&"`).
- In sweep.html, the `destAddr` is user input inserted into the result display via innerHTML. A malicious user could enter an address containing HTML/JS like `<img src=x onerror=alert(1)>`. However, this is self-XSS (the user is only attacking themselves), and the address would fail bech32 decoding before reaching the display.
- The `txid` values from mempool.space API or the server are also injected via innerHTML. If the API were compromised or MITM'd, a malicious txid could inject HTML.
- In faucet.html, the history rendering uses template literals with `h.address` and `h.txid`, which come from the server response. If the server were compromised, these could contain HTML injection payloads.
- Mitigation: Use `textContent` instead of `innerHTML` where possible, or sanitize values before HTML insertion. For the anchor tags, validate that the txid matches `^[0-9a-f]{64}$` before constructing the URL.

**Finding 2.1.2: No Content Security Policy (CSP) headers**
- Severity: Medium
- Files: All HTML files, `server.py`
- No CSP headers are set by the server or via meta tags. This means:
  - Inline scripts execute freely (all pages use inline `<script>` blocks)
  - No protection against injected scripts from browser extensions or MITM
  - No restriction on where the page can load resources from
- Mitigation: Add a CSP meta tag to each page. Since all scripts are inline, use a nonce-based CSP or `'unsafe-inline'` with `script-src 'self'`. At minimum: `<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' https://mempool.space;">`.

### 2.2 CORS and API Security

**Finding 2.2.1: Wildcard CORS on all API responses**
- Severity: Medium
- File: `server.py`, lines 399-402, 432
- All API responses include `Access-Control-Allow-Origin: *`, and the OPTIONS handler allows all origins. This means any website can make API requests to the server.
- When running in regtest mode, this allows any website to call `/api/faucet` to drain regtest coins, `/api/broadcast` to broadcast arbitrary transactions, or `/api/mine` to mine blocks.
- Mitigation: Restrict CORS to same-origin only, or at minimum to known origins. For a local development server, `localhost` origin restriction would be appropriate.

**Finding 2.2.2: Server API endpoints accept private keys in POST body**
- Severity: High (when server mode is used)
- File: `server.py`, lines 624-717 (`_handle_sweep`), 781-862 (`_handle_recover`)
- The `/api/sweep` and `/api/recover` endpoints accept WIF private keys in the POST body. The server then decodes the key, signs the transaction, and broadcasts it. This means private keys are transmitted over the network to the server.
- When running locally (`localhost`), this is lower risk. But if someone were to expose the server on a network (e.g., `0.0.0.0` binding — which is the default, line 976), private keys would be transmitted in plaintext over HTTP.
- Note: The client-side JS pages do NOT use these server endpoints for mainnet/testnet4 — they sign client-side and only call mempool.space. The server endpoints are primarily used by the Python test suite.
- Mitigation: Consider removing the server-side sweep/recover endpoints entirely, since the HTML pages now sign client-side. If retained for testing, add a warning header and ensure the server only binds to `127.0.0.1`.

**Finding 2.2.3: Server binds to 0.0.0.0 by default**
- Severity: Medium
- File: `server.py`, line 976
- `ReusableTCPServer(("0.0.0.0", port), WalletHandler)` binds to all interfaces. Combined with wildcard CORS, this exposes the API to the local network.
- Mitigation: Bind to `127.0.0.1` by default. Add a `--bind` flag for explicit network exposure.

### 2.3 Clickjacking

**Finding 2.3.1: No X-Frame-Options or frame-ancestors CSP**
- Severity: Low
- Files: All HTML files
- The pages can be framed by malicious websites. An attacker could overlay invisible frames to trick users into clicking buttons (e.g., "Generate" or "Sweep") in the context of the wallet site.
- Mitigation: Add `X-Frame-Options: DENY` header from the server, or `frame-ancestors 'none'` in CSP.

### 2.4 Input Validation

**Finding 2.4.1: Fee rate input not adequately bounded**
- Severity: Low
- File: `sweep.html`, line 449
- The fee rate input has `min="1" max="1000"`, but the JavaScript uses `parseInt()` without clamping. A user could manually set a very high fee rate that would consume nearly all funds as fees. However, the UI does show the fee amount before sweeping, so this is self-inflicted.
- The server-side sweep handler (server.py) does `int(fee_rate)` with no upper bound check.
- Mitigation: Add a sanity check — warn if the fee exceeds 50% of the total balance.

**Finding 2.4.2: Server API does not validate network parameter**
- Severity: Low
- File: `server.py`, line 934
- The `/api/broadcast` endpoint accepts a `network` parameter without validation. A crafted request could pass `network="mainnet"` when the server is in regtest mode, causing an outbound HTTP request to mempool.space with arbitrary data.
- Mitigation: Validate the `network` parameter against an allowed list. In regtest mode, only allow `network="regtest"`.

### 2.5 Subresource Integrity and Supply Chain

**Finding 2.5.1: No Subresource Integrity (SRI) on script tags**
- Severity: Info
- Files: `index.html`, `sweep.html`, `recover.html`
- The `<script src="bitcoin_crypto.js">` tags do not use SRI hashes. Since these are same-origin scripts (not CDN), this is primarily relevant if the hosting server were compromised.
- Mitigation: Add `integrity` attributes to script tags with SHA-384 hashes.

**Finding 2.5.2: Zero external dependencies — strong positive**
- Severity: Info (Positive)
- The entire cryptographic stack is self-contained. There are no `npm` dependencies, CDN loads, or external library inclusions. This eliminates the supply chain attack surface that affects most web applications. This is an excellent design decision for a security-critical application.

---

## 3. Transaction Security

### 3.1 Sighash Computation

**Finding 3.1.1: Correct BIP143 (SegWit v0) sighash implementation**
- Severity: Info (Positive)
- File: `bitcoin_crypto.js`, lines 1375-1434
- The `sighashSegwitV0Full()` implementation correctly follows BIP143 with proper hash prevouts, hash sequence, hash outputs, and SIGHASH_ALL (0x01).

**Finding 3.1.2: Correct BIP341 (Taproot) sighash for key path and script path**
- Severity: Info (Positive)
- File: `bitcoin_crypto.js`, lines 1440-1511 (key path), 1516-1603 (script path)
- Both implementations follow BIP341 correctly with proper epoch byte, spend type flags, and tagged hash usage.

### 3.2 Fee Handling

**Finding 3.2.1: No minimum output value (dust limit) check**
- Severity: Low
- Files: `sweep.html`, `recover.html`
- The sweep/recover logic checks `amountSat <= 0` but does not check for the Bitcoin dust limit (typically 546 satoshis for P2WPKH, 330 for P2TR). If the user sets a fee rate such that the output is below dust, the transaction would be rejected by nodes but the user would get an unhelpful error message.
- Mitigation: Add a dust limit check (e.g., output must be >= 546 sats) and show a clear message.

**Finding 3.2.2: Vsize estimation is hardcoded approximation**
- Severity: Low
- File: `sweep.html`, lines 726-733
- The vsize estimates (e.g., `11 + n * 69 + 31` for SegWit) are reasonable approximations but may differ slightly from actual transaction sizes. The estimates match standard Bitcoin transaction structure sizes for single-output sweeps. The only risk is slightly overpaying or underpaying fees by a few vbytes.
- This is acceptable for a sweep tool where the user sets the fee rate manually.

### 3.3 Replay Protection

**Finding 3.3.1: No cross-network replay protection mechanism**
- Severity: Info
- The transaction construction uses the same format for all networks. There is no inherent cross-network replay protection between regtest and testnet4 (both use WIF prefix 0xEF). However, addresses use different bech32 HRPs (`bcrt` vs `tb`), and the UTXOs would not exist on both networks, so practical replay is not possible.

### 3.4 Transaction Malleability

**Finding 3.4.1: SegWit and Taproot eliminate third-party malleability**
- Severity: Info (Positive)
- All transactions use SegWit (v0 or v1), which moves signatures to the witness data. This eliminates third-party transaction malleability. The txid is computed from the non-witness data only.

---

## 4. Operational Security

### 4.1 Key Exposure Risks

**Finding 4.1.1: Private keys displayed on screen and embedded in bill image**
- Severity: High (inherent to paper wallet design)
- File: `index.html`, lines 631-661
- After generation, private keys are:
  1. Displayed in the DOM as text (selectable, copyable)
  2. Embedded as QR codes and text in the bill image (PNG)
  3. Stored in the JavaScript `currentWallet` variable
  4. Present in the Canvas pixel data
- Any of these can be captured by: screen recording software, browser extensions with page access, clipboard managers, browser history (data URLs), or over-the-shoulder observation.
- This is inherent to the paper wallet concept but should be clearly communicated. The security notice does mention this, which is good.

**Finding 4.1.2: Bill image stored as data URL in DOM**
- Severity: Medium
- File: `index.html`, line 622
- The bill image is a data URL (`data:image/png;base64,...`) set as the `src` of an `<img>` tag. Data URLs can be very long and may be logged by browser extensions or network inspection tools. The entire private key (as both text and QR code) is embedded in this data URL.
- Browser history and session restore features may cache this data URL.
- Mitigation: Consider using `URL.createObjectURL()` with a Blob instead of data URLs, and explicitly revoking the URL on page unload.

**Finding 4.1.3: Backup recovery JSON file contains private key**
- Severity: Medium
- File: `index.html`, lines 695-715
- The "Download Backup Recovery" button creates a JSON file containing `backup_private_key_wif` and `internal_pubkey_hex`. This file, if intercepted or improperly stored, gives full script-path spending access to the funds.
- The file name includes the first 12 characters of the address, which creates a linkable identifier.
- Mitigation: Warn the user prominently about the sensitivity of this file. Consider encrypting it with a user-provided passphrase.

**Finding 4.1.4: `user-select: all` on key display elements**
- Severity: Low
- Files: `index.html`, `sweep.html`, `recover.html`
- The CSS `user-select: all` on `.value` elements means a single click selects the entire text, making it easy to accidentally copy private keys to the clipboard. The clipboard content may be monitored by malware.
- Mitigation: Consider removing `user-select: all` from private key fields, or warn about clipboard security.

### 4.2 Print Security

**Finding 4.2.1: No guidance on secure printing**
- Severity: Low (informational)
- The security notice recommends generating "on an offline/air-gapped computer" but does not mention:
  - Network printers may cache print jobs (containing the private key QR code)
  - Print spooler may retain a copy of the bill image
  - Cloud-connected printers (e.g., Google Cloud Print) transmit the image over the internet
- Mitigation: Add a brief printing security note.

### 4.3 Browser Extension Risk

**Finding 4.3.1: No mitigation against malicious browser extensions**
- Severity: Medium (informational)
- Browser extensions with `<all_urls>` or matching host permissions can:
  - Read all DOM content including private keys
  - Intercept `crypto.getRandomValues()` calls (replace with weak PRNG)
  - Modify the JavaScript before execution (MITM the script load)
  - Intercept fetch() calls to mempool.space
- This is a fundamental limitation of browser-based crypto. The recommendation to use an air-gapped computer partially mitigates this (no extension auto-updates, but existing extensions still run).
- Mitigation: Add a recommendation to use an incognito/private browsing window (which disables most extensions by default) or a fresh browser profile with no extensions.

---

## 5. Architecture

### 5.1 Dual-Mode Trust Model

**Finding 5.1.1: Clear separation between client-side and server-side modes**
- Severity: Info (Positive)
- The dual-mode architecture is well-designed. In static mode (GitHub Pages), all cryptography runs client-side and the server is never trusted. The mempool.space API is only used for UTXO lookups and broadcast (non-sensitive operations).

### 5.2 Command Injection

**Finding 5.2.1: Potential command injection via address parameter in bitcoin-cli**
- Severity: Medium
- File: `server.py`, lines 250-256
- The `_fetch_utxos_regtest()` function constructs a `bitcoin-cli scantxoutset` command where the `address` parameter is interpolated into a JSON string. While `json.dumps` handles the JSON encoding safely and `subprocess.run()` uses a list (not a shell string) so shell injection is not possible, `address` is user-supplied and passed directly to bitcoin-cli — malformed addresses could potentially cause unexpected behavior in bitcoin-cli's descriptor parsing.
- The `fund_address()` method also passes user-supplied `address` to `createrawtransaction`.
- Mitigation: Validate that the `address` parameter matches a bech32 address pattern before passing it to bitcoin-cli. A regex like `^(bcrt1|tb1|bc1)[a-z0-9]{25,90}$` would suffice.

**Finding 5.2.2: No request body size limit on POST endpoints**
- Severity: Low
- File: `server.py`, line 407
- `_read_json_body()` reads `Content-Length` bytes from the request body with no upper limit. An attacker could send a very large POST body to cause memory exhaustion.
- Mitigation: Add a maximum body size check (e.g., 1MB).

### 5.3 Error Handling

**Finding 5.3.1: Exception messages returned to client may leak server internals**
- Severity: Low
- File: `server.py`, lines 503, 567, 717, etc.
- All `except Exception as e: self._send_json({"error": str(e)}, 500)` patterns expose the full exception message to the client. This could leak file paths, internal state, or bitcoind error details.
- Mitigation: In production/mainnet mode, return generic error messages. Log detailed errors server-side.

---

## 6. Privacy

### 6.1 Mempool.space API Calls

**Finding 6.1.1: IP address leaked to mempool.space on UTXO lookup and broadcast**
- Severity: Medium
- Files: `sweep.html`, `recover.html`
- When checking balance or broadcasting on mainnet/testnet4, the browser makes direct fetch() calls to `https://mempool.space/api/address/{address}/utxo` and `https://mempool.space/api/tx`. This reveals:
  - The user's IP address
  - Which Bitcoin address they are interested in
  - The signed transaction (linking the source address to the destination)
- This creates a direct link between a user's IP address and their Bitcoin address/transaction.
- Mitigation: Document this privacy trade-off. Recommend using Tor Browser or a VPN for privacy-sensitive operations. Consider adding support for user-configured API endpoints (e.g., the user's own Electrum/mempool instance).

**Finding 6.1.2: Server-side mempool.space requests include identifying User-Agent**
- Severity: Low
- File: `server.py`, line 235
- The server's `_fetch_utxos_mempool()` includes `User-Agent: BitcoinGiftWallet/1.0`. This identifies the software to the mempool.space operator.
- Mitigation: Use a generic user agent or none.

### 6.2 Referrer Leaks

**Finding 6.2.1: External links may leak referrer information**
- Severity: Low
- Files: `index.html` (Reddit link), `sweep.html` (mempool.space explorer link)
- External links use `target="_blank"` without `rel="noopener noreferrer"`. When the user clicks the mempool.space transaction link, the referrer header reveals the origin page URL.
- Mitigation: Add `rel="noopener noreferrer"` to all external links.

### 6.3 Browser Fingerprinting

**Finding 6.3.1: Canvas fingerprinting possible via bill generation**
- Severity: Info (Theoretical)
- File: `bill_generator.js`
- The bill generation uses HTML5 Canvas, which renders slightly differently across browsers and hardware. A malicious website that could load this page in a frame could potentially extract a canvas fingerprint. The CSP/framing recommendations above would mitigate this.

---

## 7. Additional Findings

### 7.1 No HTTPS Enforcement

**Finding 7.1.1: No HTTPS redirect or HSTS**
- Severity: Medium (for self-hosted deployments)
- File: `server.py`
- The Python server serves over plain HTTP. If deployed on a network (not just localhost), all traffic including private keys displayed on the page would be unencrypted.
- GitHub Pages forces HTTPS, so the static deployment is fine.
- Mitigation: Add a prominent warning that the Python server must only be used on localhost. For any network deployment, HTTPS is mandatory.

### 7.2 Faucet Abuse

**Finding 7.2.1: No rate limiting on faucet endpoint**
- Severity: Low
- File: `server.py`, lines 868-895
- The `/api/faucet` endpoint has no rate limiting. An attacker could drain the regtest wallet by making rapid requests. Since this is test coins only, the impact is negligible.
- Mitigation: Add a simple rate limit (e.g., 10 requests per minute) and validate the amount server-side.

### 7.3 Test File Exposure

**Finding 7.3.1: Test HTML file accessible in production**
- Severity: Info
- File: `test_bitcoin_crypto.html`
- The test suite is served alongside the production pages (both on the Python server and GitHub Pages). The test file itself does not expose any security-sensitive functionality, but it increases the attack surface.
- Mitigation: Consider moving test files to a subdirectory excluded from production deployment, or add a `.nojekyll` + custom routing to exclude test files.

### 7.4 Timestamp in Bill Image

**Finding 7.4.1: UTC timestamp on bill can be used to narrow key generation window**
- Severity: Info
- File: `bill_generator.js`, lines 242-248
- The bill image includes a precise UTC timestamp of when it was generated. This narrows the window for a brute-force search of the CSPRNG state (if the attacker knew the platform and browser version). However, even with an exact timestamp, the CSPRNG state space is far too large to search (2^256).

---

## Summary Table

| # | Finding | Severity | Exploitable? |
|---|---------|----------|-------------|
| 1.3.1 | Non-constant-time scalar multiplication | Medium | Theoretical only |
| 1.4.1 | Private keys not zeroed in memory | Medium | Requires local access |
| 1.4.2 | Private keys in DOM via innerHTML | Medium | Via browser extensions |
| 2.1.1 | innerHTML with API/user data | Medium | Self-XSS / MITM on API |
| 2.1.2 | No Content Security Policy | Medium | Enables injection persistence |
| 2.2.1 | Wildcard CORS on server | Medium | Any website can call APIs |
| 2.2.2 | Server endpoints accept private keys | High | If server exposed on network |
| 2.2.3 | Server binds to 0.0.0.0 | Medium | Exposes API to LAN |
| 4.1.1 | Keys displayed on screen / in image | High | Inherent to design |
| 4.1.2 | Bill as data URL in DOM | Medium | Cached/logged by browser |
| 4.1.3 | Backup JSON contains private key | Medium | If file intercepted |
| 4.3.1 | No browser extension mitigation | Medium | Common attack vector |
| 5.2.1 | Address parameter to bitcoin-cli | Medium | Malformed input to CLI |
| 6.1.1 | IP leaked to mempool.space | Medium | Privacy concern |
| 7.1.1 | No HTTPS enforcement | Medium | If exposed on network |

---

## Recommendations (Priority Order)

1. **Bind server to 127.0.0.1** instead of 0.0.0.0 (server.py line 976). This is the single most impactful quick fix.

2. **Remove wildcard CORS** or restrict to same-origin (server.py lines 399, 432).

3. **Add CSP meta tags** to all HTML pages to restrict script sources and connections.

4. **Add `rel="noopener noreferrer"`** to all external links.

5. **Validate address format** before passing to bitcoin-cli in server endpoints.

6. **Add dust limit checking** in sweep/recover fee calculations.

7. **Recommend incognito/private browsing** in the security notice to mitigate browser extension risks.

8. **Use textContent instead of innerHTML** where possible, especially for displaying txids and addresses in sweep/recover results.

---

## Overall Assessment

The cryptographic core of this project is well-implemented. The key generation uses proper CSPRNG with rejection sampling, ECDSA uses RFC 6979 deterministic nonces, Schnorr follows BIP340, and the sighash computation follows BIP143/BIP341 correctly. The zero-dependency approach eliminates supply chain risk. The dual-mode architecture cleanly separates client-side crypto from server-side regtest operations.

The main areas for improvement are web security hardening (CSP, CORS, innerHTML sanitization, server binding) and operational security guidance (browser extensions, printing, clipboard). The timing side-channel concerns are theoretical and not practically exploitable in the paper wallet generation use case.

For a paper wallet generator intended for gifting Bitcoin, the security posture is reasonable. The highest real-world risks are operational — browser extensions, screen recording, insecure printing — rather than cryptographic. Users should be strongly encouraged to generate wallets on a clean, offline machine.
