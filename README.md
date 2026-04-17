# 🌐 Network Monitoring & Role-Based Access System

## 📌 Project Overview

This project is a **Network Monitoring System** built using **GNS3 and Flask**.
It simulates a real-world network environment and provides a **web-based dashboard** for monitoring devices and managing users with different roles.

---

## 🎯 Objectives

* Monitor network devices using **ping**
* Simulate two separate networks (Lab A & Lab B)
* Implement **role-based authentication system**
* Provide a simple and interactive **web dashboard**
* Manage users (students, teachers) through an admin interface

---

## 🧱 System Architecture

The system consists of:

* **GNS3 Network Simulation**

  * Lab A: `192.168.10.0/24`
  * Lab B: `192.168.20.0/24`
  * Cisco Routers connecting both labs

* **Flask Web Application**

  * Login system
  * Role-based pages
  * Device monitoring (ping)
  * Retry logic for reliability

* **Database**

  * SQLite database storing:

    * Users
    * Devices


---

## 🖥️ Features

* ✅ Role-Based Login System
* ✅ Device Monitoring using Ping
* ✅ Status Classification:

  * UP
  * DOWN
  * UNSTABLE
* ✅ Retry Mechanism
* ✅ Add/Delete Devices
* ✅ SQLite Database Integration
* ✅ Simple Web Dashboard

---

## 🧪 Network Design

* Two isolated labs connected via routers
* Each lab uses a **/24 subnet**
* Routing configured to allow communication between networks

---

## ⚠️ Design Decision (Workaround)

Due to issues with switches in GNS3, routers were used instead.

> This workaround allowed full control over IP configuration and connectivity, although it introduced some limitations compared to real Layer 2 switching.

---

## ⚙️ Installation & Setup

### 1. Clone the repository

```bash
git clone https://github.com/your-username/your-repo-name.git
cd your-repo-name
```
---

## 🔑 Default Admin Login

| Role | University ID | Password |
| ---- | ------------- | -------- |
| Tech | tech001       | admin123 |

---

## 📊 Future Improvements

* Add **SNMP monitoring**
* Real-time graphs (CPU, latency)
* Email/Telegram alerts
* Use MySQL/PostgreSQL
* Improve UI (CSS/Bootstrap)
* Add device history logs
* Implement Captive Portal

---

## ❗ Limitations

* Uses ping only (no deep monitoring)
* SQLite not suitable for large systems
* Routers used instead of switches
* Basic UI design

---

## 🏁 Conclusion

This project demonstrates a functional network monitoring system integrated with a role-based web interface. It provides a solid foundation for building more advanced network management solutions.

---

## 👨‍💻 Author

* Your Name Here

---

## 📎 Notes

This project was developed for educational purposes using GNS3 simulation and Flask web framework.
