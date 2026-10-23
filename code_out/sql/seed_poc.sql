-- POC test data: run once on EACH regional SQL database (the guide's step 5 does this for you).
-- The pipeline needs: a primary key, and an [updatedAt] column (it filters rows with
-- updatedAt <= extraction.cutoff_date in config/settings.yaml; these rows are dated 30..1 days ago).
IF OBJECT_ID('dbo.poc_orders') IS NULL
BEGIN
  CREATE TABLE dbo.poc_orders (
    id         INT           NOT NULL PRIMARY KEY,
    customer   NVARCHAR(100) NOT NULL,
    amount     DECIMAL(12,2) NOT NULL,
    region     NVARCHAR(50)  NOT NULL,
    updatedAt  DATETIME2     NOT NULL
  );
  ;WITH n AS (SELECT TOP (5000) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS i FROM sys.all_objects a CROSS JOIN sys.all_objects b)
  INSERT INTO dbo.poc_orders (id, customer, amount, region, updatedAt)
  SELECT i, CONCAT('customer-', i % 250), CAST((i * 7) % 1000 AS DECIMAL(12,2)) + 0.99, CAST(@@SERVERNAME AS NVARCHAR(50)),
         DATEADD(DAY, -(i % 30) - 1, SYSUTCDATETIME())
  FROM n;
END
SELECT COUNT(*) AS rows_loaded FROM dbo.poc_orders;
