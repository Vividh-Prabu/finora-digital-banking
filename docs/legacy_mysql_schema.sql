-- LEGACY: original MySQL schema, kept for reference only. The app now uses Firebase Realtime Database.

CREATE DATABASE IF NOT EXISTS digital_banking;
USE digital_banking;

CREATE TABLE IF NOT EXISTS CUSTOMER (
    CustomerID INT AUTO_INCREMENT PRIMARY KEY,
    FullName VARCHAR(100) NOT NULL,
    DateOfBirth DATE,
    Gender VARCHAR(20),
    Address VARCHAR(255),
    PhoneNo VARCHAR(20) UNIQUE,
    Email VARCHAR(120) UNIQUE,
    AadhaarNo VARCHAR(20) UNIQUE,
    PANNo VARCHAR(20) UNIQUE,
    MaritalStatus VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS INDIVIDUAL_CUSTOMER (
    CustomerID INT PRIMARY KEY,
    Occupation VARCHAR(100),
    AnnualIncome DECIMAL(12,2),
    FOREIGN KEY (CustomerID) REFERENCES CUSTOMER(CustomerID)
);

CREATE TABLE IF NOT EXISTS NRI_CUSTOMER (
    CustomerID INT PRIMARY KEY,
    CountryOfResidence VARCHAR(100),
    PassportNo VARCHAR(30) UNIQUE,
    FOREIGN KEY (CustomerID) REFERENCES CUSTOMER(CustomerID)
);

CREATE TABLE IF NOT EXISTS BUSINESS_CUSTOMER (
    CustomerID INT PRIMARY KEY,
    BusinessName VARCHAR(150),
    GSTNo VARCHAR(30) UNIQUE,
    BusinessType VARCHAR(100),
    FOREIGN KEY (CustomerID) REFERENCES CUSTOMER(CustomerID)
);

CREATE TABLE IF NOT EXISTS BANK_BRANCH (
    BranchID INT AUTO_INCREMENT PRIMARY KEY,
    BranchName VARCHAR(100) NOT NULL,
    Address VARCHAR(255),
    City VARCHAR(100),
    IFSCCode VARCHAR(20) NOT NULL UNIQUE,
    PhoneNo VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS ACCOUNT (
    AccountNo BIGINT AUTO_INCREMENT PRIMARY KEY,
    CustomerID INT NOT NULL,
    BranchID INT NOT NULL,
    AccountType VARCHAR(30) NOT NULL,
    OpenDate DATE NOT NULL,
    Status VARCHAR(20) DEFAULT 'Active',
    FOREIGN KEY (CustomerID) REFERENCES CUSTOMER(CustomerID),
    FOREIGN KEY (BranchID) REFERENCES BANK_BRANCH(BranchID)
);

CREATE TABLE IF NOT EXISTS `TRANSACTION` (
    TransactionID BIGINT AUTO_INCREMENT PRIMARY KEY,
    AccountNo BIGINT NOT NULL,
    TransactionDate DATETIME DEFAULT CURRENT_TIMESTAMP,
    TransactionType VARCHAR(30) NOT NULL,
    Amount DECIMAL(12,2) NOT NULL,
    Mode VARCHAR(30),
    Description VARCHAR(255),
    FOREIGN KEY (AccountNo) REFERENCES ACCOUNT(AccountNo)
);

CREATE TABLE IF NOT EXISTS EMPLOYEE (
    EmployeeID INT AUTO_INCREMENT PRIMARY KEY,
    EmpName VARCHAR(100) NOT NULL,
    Designation VARCHAR(100),
    PhoneNo VARCHAR(20),
    Email VARCHAR(120) UNIQUE,
    BranchID INT NOT NULL,
    FOREIGN KEY (BranchID) REFERENCES BANK_BRANCH(BranchID)
);

CREATE TABLE IF NOT EXISTS LOAN_APPLICATION (
    ApplicationID INT AUTO_INCREMENT PRIMARY KEY,
    CustomerID INT NOT NULL,
    EmployeeID INT NULL,
    ApplicationDate DATE NOT NULL,
    LoanType VARCHAR(50) NOT NULL,
    RequestedAmount DECIMAL(12,2) NOT NULL,
    Purpose VARCHAR(255),
    Status VARCHAR(30) DEFAULT 'Pending',
    FOREIGN KEY (CustomerID) REFERENCES CUSTOMER(CustomerID),
    FOREIGN KEY (EmployeeID) REFERENCES EMPLOYEE(EmployeeID)
);

CREATE TABLE IF NOT EXISTS LOAN (
    LoanID INT AUTO_INCREMENT PRIMARY KEY,
    ApplicationID INT NOT NULL UNIQUE,
    LoanType VARCHAR(50) NOT NULL,
    SanctionDate DATE,
    SanctionAmount DECIMAL(12,2) NOT NULL,
    InterestRate DECIMAL(5,2),
    TenureMonths INT,
    Status VARCHAR(30) DEFAULT 'Active',
    FOREIGN KEY (ApplicationID)
        REFERENCES LOAN_APPLICATION(ApplicationID)
);

CREATE TABLE IF NOT EXISTS LOAN_PAYMENT (
    PaymentID BIGINT AUTO_INCREMENT PRIMARY KEY,
    LoanID INT NOT NULL,
    PaymentDate DATE,
    InstallmentNo INT,
    AmountPaid DECIMAL(12,2) NOT NULL,
    PrincipalAmount DECIMAL(12,2),
    InterestAmount DECIMAL(12,2),
    PaymentMode VARCHAR(30),
    BalanceOutstanding DECIMAL(12,2),
    FOREIGN KEY (LoanID) REFERENCES LOAN(LoanID)
);

-- Insert one fictional branch for the demonstration.
INSERT IGNORE INTO BANK_BRANCH
    (BranchID, BranchName, Address, City, IFSCCode, PhoneNo)
VALUES
    (1, 'Demo Main Branch', '10 Sample Road', 'Vellore',
     'DEMO0000001', '9000000001');
