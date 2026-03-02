# UI Test: Testnet4 Sweep → Return (via Mempool.space)

## Overview
End-to-end UI test on Bitcoin Testnet4 using mempool.space as the backend.
Sweeps a pre-funded address to a freshly generated address, then sweeps back
to return the funds. No faucet step — requires a pre-funded Testnet4 address.

Supports both **SegWit** (`tb1q...`) and **Taproot** (`tb1p...`) funded addresses.
The sweep page address type is selected based on the funded address prefix.

**Run with:** Tell Claude Code: _"Run the UI test in test_ui_chained_testnet4.md"_

## Prerequisites
- Start server: `preview_start` with `bitcoin-gift-wallet`
- A pre-funded Testnet4 address with some tBTC (e.g., funded via https://mempool.space/testnet4/faucet)
- The WIF private key for that address
- Environment variables `TESTNET4_WIF` and `TESTNET4_ADDRESS` are set in `.claude/settings.local.json`

## Preparation

Before running this test, you need a funded Testnet4 address. Two options:

### Option A: SegWit address (simpler)
1. Generate a SegWit address externally or via the Generator
2. Fund it via https://mempool.space/testnet4/faucet
3. Wait for confirmation on https://mempool.space/testnet4
4. Save the **Private Key (WIF)** — this is `funded_wif`
5. Save the **Address** (starts with `tb1q...`) — this is `funded_address`

### Option B: Taproot + Backup address
1. Go to Generator (`/`)
2. Select **Taproot (P2TR)**, check **Enable Recovery Backup Key**, select **Testnet4**
3. Click **Generate New Wallet**
4. Copy the **Address** (starts with `tb1p...`)
5. Fund it via https://mempool.space/testnet4/faucet or any testnet4 faucet
6. Wait for confirmation on https://mempool.space/testnet4
7. Save the **Tweaked Private Key (WIF)** — this is `funded_wif`
8. Save the **Address** — this is `funded_address`

## Test Steps

### Step 0: Pre-flight Balance Check
- **Action:** Query `https://mempool.space/testnet4/api/address/{funded_address}/utxo`
- **Verify:** At least one confirmed UTXO exists with ≥1,000 sats total
- **If empty or insufficient:** STOP — ask the user to fund the address via https://mempool.space/testnet4/faucet
- **Report:** Balance in sats and number of UTXOs

### Step 1: Generate a Destination Address (Taproot + Backup)
- **Page:** Generator (`/`)
- **Actions:**
  1. Select **Taproot (P2TR)** address type
  2. Check **Enable Recovery Backup Key**
  3. Select **Testnet4** network
  4. Click **Generate New Wallet**
- **Screenshot checkpoint**
- **Capture:** `dest_address` (starts with `tb1p...`), `dest_bill_wif` (tweaked WIF)

### Step 2: Sweep Funded Address → Destination
- **Page:** Sweep (`/sweep.html`)
- **Actions (Step 1 - Enter Key):**
  1. Enter `funded_wif` in Private Key field
  2. Select address type based on `funded_address` prefix:
     - `tb1q...` → **SegWit (P2WPKH)**
     - `tb1p...` → **Taproot (tweaked)**
  3. Select **Testnet4** network
  4. Click **Derive Address**
- **Verify:** Derived address matches `funded_address`
- **Screenshot checkpoint**
- **Actions (Step 2 - Check Balance):**
  5. Click **Check Balance**
- **Verify:** Balance shows available tBTC
- **Screenshot checkpoint**
- **Actions (Step 3 - Sweep):**
  6. Enter `dest_address` in Destination Address field
  7. Set fee rate to 2 sat/vB
  8. Click **Sweep All Funds**
- **Verify:** Transaction broadcast (may show "Broadcast" not "Confirmed" since testnet4 blocks take ~10 min)
- **Screenshot checkpoint**
- **Capture:** `sweep_txid`, `sweep_amount`, `sweep_fee`

### Step 3: Return Funds — Sweep Destination → Original Address
- **Note:** No need to wait for confirmation. The return sweep spends the unconfirmed output from Step 2.
- **Page:** Sweep (`/sweep.html`)
- **Actions (Step 1 - Enter Key):**
  1. Enter `dest_bill_wif` in Private Key field
  2. Select **Taproot (tweaked)** address type (destination is always Taproot+backup)
  3. Select **Testnet4** network
  4. Click **Derive Address**
- **Verify:** Derived address matches `dest_address`
- **Actions (Step 2 - Check Balance):**
  5. Click **Check Balance**
- **Verify:** Balance matches `sweep_amount`
- **Actions (Step 3 - Sweep):**
  6. Enter `funded_address` in Destination Address field
  7. Set fee rate to 2 sat/vB
  8. Click **Sweep All Funds**
- **Verify:** Transaction broadcast
- **Screenshot checkpoint**
- **Capture:** `return_txid`, `return_amount`, `return_fee`

### Step 4: Verify Fee Chain
- **Check:** `sweep_fee + return_fee` = total deducted from original funding
- **Check:** Funds are back at `funded_address` (minus fees)
- **Report:** Final summary table

## Expected Result

| Step | From | To | Amount | Fee |
|------|------|----|--------|-----|
| Sweep | funded_address | dest_address | ~N sats | ~140-224 sats |
| Return | dest_address | funded_address | ~N-fee sats | ~224 sats |

Fee depends on address type: SegWit inputs are ~140 sats at 2 sat/vB, Taproot ~224 sats.

The return sweep spends an unconfirmed output (no confirmation wait needed). Both transactions are visible on https://mempool.space/testnet4. Print the explorer links for both TXIDs at the end so they can be traced back. Funds returned to original address (minus total fees).
