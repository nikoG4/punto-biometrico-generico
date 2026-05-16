CREATE DATABASE IF NOT EXISTS biometric_attendance
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'biometric_user'@'%' IDENTIFIED BY 'biometric_pass';
GRANT ALL PRIVILEGES ON biometric_attendance.* TO 'biometric_user'@'%';
FLUSH PRIVILEGES;

USE biometric_attendance;

CREATE TABLE IF NOT EXISTS employees (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(160) NOT NULL,
  document_id VARCHAR(80) UNIQUE,
  external_id VARCHAR(80) UNIQUE,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS face_embeddings (
  id INT AUTO_INCREMENT PRIMARY KEY,
  employee_id INT NOT NULL,
  embedding LONGBLOB NOT NULL,
  dimension INT NOT NULL DEFAULT 512,
  provider VARCHAR(40) NOT NULL DEFAULT 'insightface',
  image_snapshot_path TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX ix_face_embeddings_employee_id (employee_id),
  CONSTRAINT fk_face_embeddings_employee
    FOREIGN KEY (employee_id) REFERENCES employees(id)
);

CREATE TABLE IF NOT EXISTS devices (
  id VARCHAR(80) PRIMARY KEY,
  location VARCHAR(160),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS attendance_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  employee_id INT NOT NULL,
  timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  type ENUM('IN','OUT') NOT NULL,
  device_id VARCHAR(80) NOT NULL,
  confidence FLOAT NOT NULL,
  image_snapshot_path TEXT,
  synced BOOLEAN NOT NULL DEFAULT TRUE,
  INDEX ix_attendance_employee_time (employee_id, timestamp),
  INDEX ix_attendance_device_id (device_id),
  CONSTRAINT fk_attendance_employee
    FOREIGN KEY (employee_id) REFERENCES employees(id),
  CONSTRAINT fk_attendance_device
    FOREIGN KEY (device_id) REFERENCES devices(id)
);

CREATE TABLE IF NOT EXISTS biometric_faces (
  id INT AUTO_INCREMENT PRIMARY KEY,
  local_employee_id INT NULL,
  local_face_embedding_id INT NULL,
  device_id VARCHAR(80) NOT NULL,
  person_name VARCHAR(160) NULL,
  employee_marker_code INT NULL,
  embedding LONGBLOB NOT NULL,
  dimension INT NOT NULL DEFAULT 512,
  provider VARCHAR(40) NOT NULL DEFAULT 'insightface',
  image_snapshot LONGBLOB NOT NULL,
  local_snapshot_path TEXT,
  status ENUM('PENDING','LINKED','IGNORED') NOT NULL DEFAULT 'PENDING',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NULL DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
  linked_at DATETIME NULL,
  linked_by INT NULL,
  UNIQUE KEY uk_biometric_faces_device_embedding (device_id, local_face_embedding_id),
  INDEX ix_biometric_faces_status (status),
  INDEX ix_biometric_faces_marker_code (employee_marker_code)
);
