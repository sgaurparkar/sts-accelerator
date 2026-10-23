-- Probe 118: BIT column compared with 1
CREATE PROCEDURE dbo.probe_118_bit_compare
AS
BEGIN
    SELECT Id FROM dbo.T WHERE IsActive = 1;
END
GO
