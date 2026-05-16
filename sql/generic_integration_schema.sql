CREATE TABLE IF NOT EXISTS employees (
  id INT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(80) NOT NULL UNIQUE,
  name VARCHAR(160) NOT NULL,
  status VARCHAR(40) NOT NULL DEFAULT 'ACTIVE',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS attendance_events (
  id INT AUTO_INCREMENT PRIMARY KEY,
  employee_code VARCHAR(80) NOT NULL,
  timestamp DATETIME NOT NULL,
  type ENUM('IN','OUT') NOT NULL,
  device_id VARCHAR(80) NOT NULL,
  confidence FLOAT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX ix_attendance_events_employee_time (employee_code, timestamp),
  CONSTRAINT fk_attendance_events_employee
    FOREIGN KEY (employee_code) REFERENCES employees(code)
    ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS biometric_faces (
  id INT AUTO_INCREMENT PRIMARY KEY,
  local_employee_id INT NULL,
  local_face_embedding_id INT NULL,
  device_id VARCHAR(80) NOT NULL,
  person_name VARCHAR(160) NULL,
  rrhh_employee_id INT NULL,
  employee_marker_code VARCHAR(80) NULL,
  embedding LONGBLOB NOT NULL,
  dimension INT NOT NULL DEFAULT 512,
  provider VARCHAR(40) NOT NULL DEFAULT 'insightface',
  image_snapshot LONGBLOB NOT NULL,
  local_snapshot_path TEXT NULL,
  status ENUM('PENDING','LINKED','IGNORED') NOT NULL DEFAULT 'PENDING',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NULL DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
  linked_at DATETIME NULL,
  linked_by VARCHAR(80) NULL,
  UNIQUE KEY uk_biometric_faces_device_embedding (device_id, local_face_embedding_id),
  INDEX ix_biometric_faces_status (status),
  INDEX ix_biometric_faces_marker_code (employee_marker_code)
);
