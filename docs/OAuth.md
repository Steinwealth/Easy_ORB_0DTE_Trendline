# OAuth Token Management Guide
## Easy ORB Strategy - Complete E*TRADE OAuth System Documentation

**Last Updated**: February 13, 2026  
**Version**: Rev 00260 (Production token only; Cloud Cleanup Automation)  
**Purpose**: Complete user guide for the E*TRADE OAuth token management system, covering token acquisition, renewal, automated keep-alive, and integration with the trading system.

**⚠️ Note**: For sensitive deployment-specific information (OAuth portal URLs, access codes, service URLs), see [PrivateSecrets.md](PrivateSecrets.md).

---

## 📋 **Table of Contents**

1. [OAuth System Overview](#oauth-system-overview)
2. [Token Lifecycle](#token-lifecycle)
3. [OAuth Architecture](#oauth-architecture)
4. [Web App Usage](#web-app-usage)
5. [Automated Token Management](#automated-token-management)
6. [Token Storage and Security](#token-storage-and-security)
7. [Integration with Trading System](#integration-with-trading-system)
8. [Troubleshooting](#troubleshooting)
9. [Best Practices](#best-practices)

---

## 🔐 **OAuth System Overview**

The E*TRADE OAuth system is **critical** for continuous trading operations. E*TRADE tokens expire at **midnight ET every day** and require daily renewal to maintain uninterrupted trading.

### **Why OAuth Tokens Matter**

- **Trading Interruption**: Expired tokens will stop all trading operations
- **Position Risk**: Open positions cannot be managed without valid tokens
- **Data Access**: Market data and account information become unavailable
- **Revenue Loss**: Missed trading opportunities during token downtime

### **Token Lifecycle Rules**

- **Daily Expiry**: E*TRADE tokens expire at **midnight ET every day**
- **Idle Timeout**: Tokens become inactive after **2 hours** of no API calls
- **Renewal Window**: Inactive tokens can be renewed (no re-authorization needed)
- **Expiration**: Expired tokens require full re-authentication

**Important**: E*TRADE does not have separate sandbox/production APIs. Both Demo and Live modes use the **production E*TRADE API** and the same production OAuth tokens. The difference is which account is used (demo account vs live account).

### **System Components**

1. **Firebase Web App**: Public dashboard and management portal
2. **OAuth Backend Service**: Cloud Run service for token management
3. **Secret Manager**: Secure token storage
4. **Cloud Scheduler**: Automated keep-alive jobs
5. **Alert System**: Telegram notifications for token status

---

## ⏰ **Token Lifecycle**

### **Token States**

1. **Active**: Token is valid and can be used for API calls
2. **Idle**: Token hasn't been used for 2+ hours (can be renewed without re-auth)
3. **Expired**: Token expired at midnight ET (requires full re-authentication)

### **Daily Token Renewal Flow**

```
Midnight ET (12:00 AM)
  ↓
Tokens Expire
  ↓
Telegram Alert Sent (12:00 AM ET)
  ↓
User Visits Web App
  ↓
Clicks "Renew Tokens" (or similar renewal button)
  ↓
Redirects to Management Portal
  ↓
Enters Access Code
  ↓
Completes OAuth Flow (PIN authorization)
  ↓
Tokens Stored in Secret Manager
  ↓
Telegram Confirmation Alert
  ↓
Trading System Loads Fresh Tokens
  ↓
Trading Continues (no restart needed)
```

### **Token Renewal Timing**

- **Best Practice**: Renew tokens before midnight ET
- **Emergency**: Can renew after expiry (requires full re-auth)
- **Automated**: Cloud Scheduler keep-alive prevents idle timeout

---

## 🏗️ **OAuth Architecture**

### **System Components**

```
┌─────────────────────────────────────────────────────────────┐
│                    OAuth System Architecture                │
├─────────────────────────────────────────────────────────────┤
│  Firebase Web App (Frontend)                                │
│  ├── Public Dashboard (index.html)                          │
│  │   └── Token status, countdown timer                      │
│  └── Management Portal (manage.html)                         │
│      └── Token renewal interface (password-protected)       │
├─────────────────────────────────────────────────────────────┤
│  OAuth Backend Service (Cloud Run)                          │
│  ├── FastAPI Backend (oauth_backend.py)                      │
│  ├── Secret Manager Integration                              │
│  ├── Direct Telegram API (alerts)                           │
│  └── OAuth Flow Handler                                      │
├─────────────────────────────────────────────────────────────┤
│  Google Secret Manager                                      │
│  ├── etrade-oauth-prod (Production tokens - Demo & Live)   │
│  ├── etrade-prod-consumer-key (Production consumer key)     │
│  └── etrade-prod-consumer-secret (Production consumer secret)│
├─────────────────────────────────────────────────────────────┤
│  Cloud Scheduler Jobs                                       │
│  ├── oauth-midnight-alert (12:00 AM ET daily)               │
│  └── oauth-keepalive-prod (Hourly at :00)                    │
├─────────────────────────────────────────────────────────────┤
│  Trading System Integration                                 │
│  ├── Automatic Token Loading (from Secret Manager)          │
│  ├── Token Validation                                       │
│  └── Graceful Error Handling                                │
└─────────────────────────────────────────────────────────────┘
```

### **OAuth Flow Types**

#### **OAuth 1.0a Flow (Current Implementation)**

The Easy ORB Strategy uses **OAuth 1.0a** (3-legged OAuth) for E*TRADE API authentication:

1. **Request Token**: Get temporary request token from E*TRADE
2. **User Authorization**: User authorizes application on E*TRADE website
3. **Access Token**: Exchange authorized request token for access token
4. **API Access**: Use access token for all API calls

#### **Token Types**

**Request Token** (Temporary):
- Used only during authorization flow
- Expires immediately after authorization
- Cannot be used for API calls

**Access Token** (Permanent):
- Used for all API calls
- Expires at midnight ET daily
- Can be renewed without re-authorization (if not expired)

---

## 🌐 **Web App Usage**

### **Accessing the OAuth Web App**

**Public Dashboard**: `https://easy-trading-oauth-v2.web.app`  
**Management Portal**: `https://easy-trading-oauth-v2.web.app/manage.html` (Access code required - see PrivateSecrets.md)

**Features**:
- ⏰ Real-time countdown timer to midnight ET
- 📊 Live OAuth system status
- 🔄 Token renewal interface
- 📱 Mobile-friendly interface
- 🔔 Telegram alerts integration
- 🔒 Password-protected management portal

**Note**: Both Demo and Live modes use the same production E*TRADE OAuth tokens since they use the same production API. The difference is which account is used (demo account vs live account).

### **Daily Token Renewal Process**

#### **Step 1: Check Token Status**

1. Visit the public dashboard: `https://easy-trading-oauth-v2.web.app`
2. View current token status:
   - ✅ **Valid**: Green indicator, shows time until expiry
   - ❌ **Expired**: Red indicator, shows "EXPIRED"
   - ⚠️ **Unknown**: Gray indicator, check system

#### **Step 2: Initiate Renewal**

1. Click **"Renew Tokens"** button (or similar renewal button)
2. You'll be redirected to the management portal
3. Enter the access code (see PrivateSecrets.md)
4. Complete the OAuth renewal process on the portal

#### **Step 3: Complete OAuth Flow**

1. **OAuth Flow Starts**: Automatically begins E*TRADE authorization
2. **Authorize on E*TRADE**: Click link to complete authorization on E*TRADE website
3. **Copy PIN**: Get 6-digit PIN from E*TRADE authorization page
4. **Paste PIN**: Return to portal and enter PIN to complete OAuth
5. **Automatic Storage**: Fresh tokens stored in Google Secret Manager
6. **Telegram Confirmation**: Immediate alert sent via Telegram
7. **System Integration**: Trading system automatically loads tokens from Secret Manager
8. **Trading Ready**: System continues - no restart required

**Note**: Both Demo and Live modes use the same production E*TRADE OAuth tokens. The tokens are shared between modes since they use the same production API.

### **Web App Features**

- **⏰ Countdown Timer**: Real-time countdown to midnight ET
- **📊 Status Dashboard**: Live OAuth system status
- **🔄 Token Renewal**: Renew production OAuth tokens (used by both Demo and Live modes)
- **📱 Mobile Optimized**: Perfect on phones and tablets
- **🔔 Telegram Alerts**: Immediate confirmation alerts via direct Telegram API
- **🔒 Portal Flexibility**: Either portal URL can renew either token
- **📡 24/7 Availability**: Alerts work regardless of trading system state
- **🔔 Real-time Updates**: Live status monitoring with automatic refresh

---

## 🤖 **Automated Token Management**

### **Cloud Scheduler Jobs**

#### **1. OAuth Midnight Alert**

**Schedule**: 12:00 AM ET daily  
**Purpose**: Alert when tokens expire at midnight  
**Endpoint**: OAuth backend `/cron/midnight-expiry-alert`  
**Delivery**: Direct Telegram API (works 24/7)

**Alert Content**:
- Token expiry notification
- Public dashboard URL
- Renewal instructions
- Access code reminder

#### **2. OAuth Production Keep-Alive**

**Schedule**: Hourly at :00 (0:00, 1:00, 2:00, etc.)  
**Purpose**: Prevent idle timeout (tokens become inactive after 2 hours)  
**Endpoint**: OAuth backend `/api/oauth/keepalive/prod`  
**Action**: Refreshes production tokens if idle

**Note**: Both Demo and Live modes use the same production E*TRADE OAuth tokens, so only one keep-alive job is needed for production tokens.

### **Keep-Alive Benefits**

- **Prevents Idle Timeout**: Tokens stay active even when not actively trading
- **Automatic Renewal**: Tokens renewed before 2-hour idle timeout
- **No Manual Intervention**: Fully automated token maintenance
- **24/7 Operation**: System runs continuously without token issues

### **Good Morning Alert**

**Schedule**: 5:30 AM PT (8:30 AM ET) daily  
**Purpose**: System status check and token validation  
**Content**:
- Token status (valid/expired)
- Configuration mode (Demo/Live)
- System health check
- Trading readiness status

**Features**:
- Time validation: Only sends between 5:30-5:35 AM PT
- Deduplication: One alert per day maximum (GCS-based)
- Protection: Rejects calls outside valid window

---

## 🔒 **Token Storage and Security**

### **Google Secret Manager Integration**

**Purpose**: Secure, encrypted storage for OAuth tokens and credentials

**Secret Structure**:
```
Project: YOUR_PROJECT_ID
Secrets:
  ├── etrade-oauth-prod          # Production OAuth tokens (JSON) - used by both Demo and Live modes
  ├── etrade-prod-consumer-key   # Production consumer key - used by both Demo and Live modes
  └── etrade-prod-consumer-secret # Production consumer secret - used by both Demo and Live modes
  ├── telegram-bot-token         # Telegram bot token (for alerts)
  └── telegram-chat-id          # Telegram chat ID (for alerts)
```

### **Token Storage Format**

**OAuth Tokens** (JSON format):
```json
{
  "oauth_token": "oauth_access_token",
  "oauth_token_secret": "oauth_access_secret",
  "created_at": "2026-01-06T16:21:19.899869+00:00",
  "last_used": "2026-01-06T16:21:19.899869+00:00",
  "stored_at": "2026-01-06T16:21:19.899869+00:00",
  "environment": "prod",
  "project_id": "YOUR_PROJECT_ID",
  "expires_at": "2026-01-06T23:59:59.899341+00:00"
}
```

**Consumer Credentials** (Plain text):
```
Consumer Key Secret: "your_consumer_key_here"
Consumer Secret Secret: "your_consumer_secret_here"
```

### **Secret Manager Benefits**

- **🔒 Encrypted Storage**: All credentials and tokens encrypted at rest
- **🛡️ IAM Control**: Fine-grained access control with service accounts
- **🔄 Versioning**: Automatic secret versioning for rollback capability
- **📊 Audit Logs**: Complete access logging and monitoring
- **🌐 Global Access**: Access from any Google Cloud service
- **💰 Cost Effective**: ~$1.20/month (20 billable versions × $0.06) with automatic cleanup
- **🚀 Production Ready**: Integrated with Firebase frontend for daily renewal
- **🧹 Automatic Cleanup**: ✅ Deployed (February 9, 2026) - Old versions automatically deleted on token renewal

### **Security Architecture**

**Two-Tier Design**:
1. **Public Dashboard**: Token status and countdown (no sensitive data)
2. **Management Portal**: Password-protected token renewal (access code required)

**Anti-Phishing Measures**:
- Public dashboard shows legitimate system information
- Management portal is password-protected and not indexed
- Complete transparency and legitimate identification
- Google Cloud Acceptable Use Policy compliant

---

## 🔄 **Integration with Trading System**

### **Token Loading**

The trading system automatically loads tokens from Google Secret Manager:

**Loading Process**:
1. System starts up
2. Checks `ENVIRONMENT` variable (production vs development)
3. If production: Loads from Secret Manager
4. If development: Loads from `secretsprivate/` folder
5. Validates token format and expiry
6. Initializes E*TRADE API client with tokens

**Code Location**: `modules/prime_etrade_trading.py` → `_load_tokens_from_secret_manager()`

### **Token Usage**

**Trading System**:
- Loads tokens from Secret Manager at startup
- Automatically refreshes if tokens are renewed
- Uses tokens for all E*TRADE API calls
- Handles token expiration gracefully

**OAuth Backend**:
- Stores renewed tokens in Secret Manager
- Sends Telegram alerts on renewal
- Provides web interface for token management

### **Token Validation**

**Validation Checks**:
- Token format validation
- Expiry date checking
- Last used timestamp tracking
- Environment validation (production API for both Demo and Live modes)

**Error Handling**:
- Graceful degradation on token errors
- Automatic retry on transient failures
- Alert notifications for token issues
- Fallback to manual renewal if needed

---

## 📱 **Alert System Integration**

### **OAuth Alerts**

**Alert Types**:
- **OAuth Token Renewed**: When OAuth tokens are renewed (used by both Demo and Live modes)
- **OAuth Tokens Expired**: At midnight ET (daily expiry alert)
- **OAuth Morning Alert**: Token status check at 5:30 AM PT

**Alert Delivery**:
- **Direct Telegram API**: Works 24/7, independent of trading system
- **Portal Agnostic**: Alert matches token renewed, not portal URL
- **Guaranteed Delivery**: Secret Manager credentials configured

**Alert Examples**:

**Token Expiry Alert**:
```
====================================================================

⚠️ <b>OAuth Tokens Expired</b>
          Time: 09:00 PM PT (12:00 AM ET)

🚨 <b>Token Status:</b>
          E*TRADE tokens are <b>EXPIRED</b> ❌

🌐 <b>Public Dashboard:</b>
          https://easy-trading-oauth-v2.web.app

⚠️ Renew OAuth Tokens (used by both Demo and Live modes)

👉 <b>Action Required:</b>
1. Visit the public dashboard
2. Click "Renew Tokens" (or similar renewal button)
3. Enter access code (see PrivateSecrets.md)
4. Complete OAuth authorization
5. Tokens will be renewed and stored

====================================================================
```

**Token Renewed Alert**:
```
====================================================================

✅ <b>OAuth Tokens Renewed</b>
          Time: 10:15 PM PT (01:15 AM ET)

🔄 <b>Token Status:</b>
          OAuth tokens successfully renewed ✅
          (Used by both Demo and Live modes)

📊 <b>Next:</b> Trading system will automatically load fresh tokens
          (No restart required)

====================================================================
```

---

## 🛠️ **Troubleshooting**

### **Common Issues**

#### **1. Tokens Not Renewing**

**Symptoms**: Tokens expire, renewal fails  
**Solutions**:
- ✅ Verify access code is correct (see PrivateSecrets.md)
- ✅ Check OAuth backend service is running
- ✅ Verify Secret Manager permissions
- ✅ Check Cloud Scheduler jobs are enabled
- ✅ Review OAuth backend logs

#### **2. Tokens Expired But No Alert**

**Symptoms**: Tokens expired, no Telegram alert received  
**Solutions**:
- ✅ Check Telegram bot token and chat ID configuration
- ✅ Verify Cloud Scheduler job is enabled (`oauth-midnight-alert`)
- ✅ Check OAuth backend service logs
- ✅ Verify Secret Manager has Telegram credentials

#### **3. Renewal Fails on Portal**

**Symptoms**: OAuth flow starts but fails during PIN entry  
**Solutions**:
- ✅ Verify PIN is correct (6 digits)
- ✅ Check PIN hasn't expired (PINs expire quickly)
- ✅ Try renewal again (get fresh PIN)
- ✅ Verify E*TRADE account is active
- ✅ Check OAuth backend service logs

#### **4. Trading System Can't Load Tokens**

**Symptoms**: Trading system reports token errors  
**Solutions**:
- ✅ Verify Secret Manager secrets exist
- ✅ Check service account permissions
- ✅ Verify secret names match configuration
- ✅ Review trading system logs
- ✅ Manually renew tokens via web app

#### **5. Keep-Alive Not Working**

**Symptoms**: Tokens become idle despite keep-alive jobs  
**Solutions**:
- ✅ Verify Cloud Scheduler jobs are enabled
- ✅ Check OAuth backend service is accessible
- ✅ Review keep-alive job logs
- ✅ Verify keep-alive endpoints are working
- ✅ Check Secret Manager access

### **Useful Commands**

```bash
# Check OAuth backend service status
gcloud run services describe easy-etrade-strategy-oauth \
    --region=us-central1

# View OAuth backend logs
gcloud run services logs read easy-etrade-strategy-oauth \
    --region=us-central1 --limit 50

# Check Cloud Scheduler jobs
gcloud scheduler jobs list --location=us-central1

# Test OAuth keep-alive endpoint
curl -X POST https://YOUR_OAUTH_SERVICE_URL/api/oauth/keepalive/prod

# Check Secret Manager secrets
gcloud secrets list --project=YOUR_PROJECT_ID

# View secret (without revealing value)
gcloud secrets describe etrade-oauth-prod --project=YOUR_PROJECT_ID
```

---

## ✅ **Best Practices**

### **Daily Operations**

#### **Morning Checklist**
1. **Check Token Status**: Verify tokens are active (Good Morning alert)
2. **Review Alerts**: Check for any OAuth issues overnight
3. **Verify Trading**: Confirm trading system is using valid tokens
4. **Monitor Keep-Alive**: Ensure keep-alive jobs are running

#### **Evening Checklist**
1. **Check Token Health**: Verify tokens are still valid
2. **Prepare for Renewal**: Ensure renewal process is ready
3. **Review Logs**: Check for any OAuth errors
4. **Backup Tokens**: Note current token status

### **Token Renewal Best Practices**

1. **Renew Before Midnight**: Renew tokens before 12:00 AM ET to avoid expiry
2. **Use Web App**: Always use the web app for token renewal (not manual methods)
3. **Verify Renewal**: Check Telegram confirmation alert after renewal
4. **Test Connection**: Verify API connectivity after renewal
5. **Monitor Alerts**: Watch for OAuth-related alerts

### **Security Best Practices**

1. **Protect Access Code**: Never share the management portal access code
2. **Use Secret Manager**: Always store tokens in Secret Manager (production)
3. **Rotate Credentials**: Rotate consumer keys/secrets periodically
4. **Monitor Access**: Review Secret Manager access logs regularly
5. **Use HTTPS**: Always use HTTPS for web app access

### **Emergency Procedures**

#### **Complete Token Failure**

1. **Stop Trading**: Stop all trading operations immediately
2. **Emergency Renewal**: Renew tokens via web app
3. **Test Connection**: Verify API connectivity
4. **Restart System**: Restart trading system if needed
5. **Monitor Alerts**: Watch for confirmation alerts

#### **Partial Token Failure**

1. **Check Token Status**: Check token status via web app dashboard
2. **Renew Specific Token**: Renew failed token via web app
3. **Verify Renewal**: Check Telegram confirmation alert
4. **Verify Trading**: Confirm trading system loads fresh tokens
5. **Monitor System**: Watch for any additional issues

---

## 📊 **System Status and Monitoring**

### **Current System Status**

**OAuth System**: ✅ Production Active  
**Web App**: ✅ Live (Anti-Phishing Secure)  
**OAuth Backend**: ✅ Live (Cloud Run, scale-to-zero)  
**Secret Manager**: ✅ Configured  
**Alert System**: ✅ Active (Telegram notifications)  
**Keep-Alive System**: ✅ Automated (Cloud Scheduler)  
**Mobile Interface**: ✅ Responsive design  

### **Monitoring**

**Key Metrics**:
- Token renewal success rate
- Token expiry alerts sent
- Keep-alive job execution
- OAuth backend service health
- Secret Manager access logs

**Alert Monitoring**:
- Token expiry alerts
- Renewal confirmation alerts
- OAuth error alerts
- System status alerts

---

## 🔗 **Integration Points**

### **Trading System Integration**

- **Automatic Token Loading**: Tokens loaded from Secret Manager at startup
- **Token Validation**: System validates tokens before use
- **Error Handling**: Graceful handling of token expiration
- **Alert Integration**: OAuth alerts integrated with trading alerts

### **Cloud Scheduler Integration**

- **Midnight Alert**: Daily token expiry notification
- **Keep-Alive Jobs**: Hourly token refresh
- **Good Morning Alert**: System status check

### **Alert System Integration**

- **Telegram Alerts**: Direct Telegram API for 24/7 availability
- **Alert Types**: Token expiry, renewal confirmation, errors
- **Alert Delivery**: Independent of trading system state

---

## 📝 **Revision History**

### **Latest Updates (February 9, 2026)**

**Secret Manager Cleanup Deployment (Feb 9, 2026)**:
- ✅ Firebase OAuth app deployed with automatic secret cleanup
- ✅ Cleanup code active in `oauth_backend.py` (Firebase Functions)
- ✅ Automatic cleanup on every token renewal
- ✅ Current cost: ~$1.20/month (down from $198/month)
- ✅ Versions stay at 1 per secret automatically

**Rev 00259 (Jan 22 - Cloud Cleanup Automation)**:
- ✅ Comprehensive OAuth documentation
- ✅ Complete token management guide
- ✅ Sensitive information moved to PrivateSecrets.md

### **Previous Updates**

**Rev 00246 (Jan 19 - 0DTE Strategy Improvements)**:
- ✅ OAuth system fully operational
- ✅ Web app deployed and working
- ✅ Keep-alive system automated

**Rev 00233 (Jan 8 - Secrets Management)**:
- ✅ Two-tier secrets management system
- ✅ All OAuth credentials in Secret Manager
- ✅ Secure token storage

---

## 📚 **Additional Resources**

- **Alert System**: See [Alerts.md](Alerts.md) for OAuth alert details
- **Cloud Deployment**: See [Cloud.md](Cloud.md) for OAuth service deployment; [CloudSecrets.md](CloudSecrets.md) for scripts deployed with app
- **Settings**: See [Settings.md](Settings.md) for OAuth configuration
- **Sensitive Information**: See [PrivateSecrets.md](PrivateSecrets.md) for OAuth portal URLs and access codes

---

**OAuth Token Management Guide - Complete and Ready for Use!** 🔐

*Last Updated: February 13, 2026*  
*Version: Rev 00260 (Production token only; Cloud Cleanup Automation)*  
*Maintainer: Easy ORB Strategy Development Team*
