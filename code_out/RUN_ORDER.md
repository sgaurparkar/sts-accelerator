# Run order  (A = Azure Cloud Shell, PowerShell   |   G = Google Cloud Shell, bash)
Folders:  1_code  = the pipeline code (upload to Google Cloud Shell, unzip as ~/sts-accelerator-main)
          2_azure_powershell = PowerShell steps (upload to Azure Cloud Shell > PowerShell)
          3_gcp_terminal     = bash steps (upload to Google Cloud Shell)
Edit the <placeholders> in each script first, and set $sfx in 00_variables.ps1.

 1  A  ./01_azure_network.ps1        vWAN, 4 hubs, VPN gateway in hub-na (starts a 30-45 min job)
 2  G  export PROJECT_ID=<id> ; bash 02_gcp_network.sh     VPC, subnets, HA VPN gateway, router. Note the 2 Google addresses
 3  A  ./03_azure_workloads.ps1      VNets, 5 SQL servers + database, SQL private endpoints, hub links. Prints the settings.yaml regions block
 4  A  ./10_secrets.ps1              prints the .env lines (SQL login per region)
 5  A  ./04_azure_vpn.ps1            (gateway must be Succeeded) VPN site + connection + custom IPsec policy, then the portal custom-BGP step. Note the 2 Azure addresses
 6  G  bash 05_gcp_tunnels.sh        tunnels + BGP
 7  G  bash 06_verify.sh   /   A  ./06_verify.ps1     tunnels Established, BGP Up
 8  A  ./07_print_dns_commands.ps1   prints 5 gcloud lines
 9  G  bash 07_gcp_dns_buckets.sh    DNS zone (paste the 5 lines when asked), then Terraform buckets/datasets
10  G  bash 08_runner_vm.sh          runner VM + NAT, copies the code, logs you in
11  VM bash 09_on_runner.sh          setup, fill .env and settings.yaml, --check-network, then the real run

## Multi-project mode
See MULTI_PROJECT.md (one GCP project per Azure resource group, created automatically).
