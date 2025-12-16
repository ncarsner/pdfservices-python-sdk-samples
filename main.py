"""
 Copyright 2024 Adobe
 All Rights Reserved.

 NOTICE: Adobe permits you to use, modify, and distribute this file in
 accordance with the terms of the Adobe license agreement accompanying it.
"""

import json
import logging
import os
import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from adobe.pdfservices.operation.auth.service_principal_credentials import ServicePrincipalCredentials
from adobe.pdfservices.operation.exception.exceptions import ServiceApiException, ServiceUsageException, SdkException
from adobe.pdfservices.operation.io.cloud_asset import CloudAsset
from adobe.pdfservices.operation.io.stream_asset import StreamAsset
from adobe.pdfservices.operation.pdf_services import PDFServices
from adobe.pdfservices.operation.pdf_services_media_type import PDFServicesMediaType
from adobe.pdfservices.operation.pdfjobs.jobs.autotag_pdf_job import AutotagPDFJob
from adobe.pdfservices.operation.pdfjobs.jobs.pdf_accessibility_checker_job import PDFAccessibilityCheckerJob
from adobe.pdfservices.operation.pdfjobs.params.autotag_pdf.autotag_pdf_params import AutotagPDFParams
from adobe.pdfservices.operation.pdfjobs.result.autotag_pdf_result import AutotagPDFResult
from adobe.pdfservices.operation.pdfjobs.result.pdf_accessibility_checker_result import PDFAccessibilityCheckerResult

# Initialize the logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class PDFAccessibilityProcessor:
    """
    Batch process PDFs for ADA compliance by checking accessibility,
    auto-tagging, and re-checking accessibility.
    """

    def __init__(self, credentials_path: str = "./pdfservices-api-credentials.json"):
        """
        Initialize the processor with Adobe PDF Services credentials.
        
        Args:
            credentials_path: Path to the credentials JSON file
        """
        self.credentials_path = credentials_path
        self.credentials = self._load_credentials()
        self.pdf_services = PDFServices(credentials=self.credentials)

    def _load_credentials(self) -> ServicePrincipalCredentials:
        """Load credentials from the configuration file."""
        try:
            with open(self.credentials_path, "r") as config_file:
                config = json.load(config_file)
            
            return ServicePrincipalCredentials(
                client_id=config["client_credentials"]["client_id"],
                client_secret=config["client_credentials"]["client_secret"]
            )
        except FileNotFoundError:
            logger.error(f"Credentials file not found at {self.credentials_path}")
            raise
        except KeyError as e:
            logger.error(f"Missing required key in credentials file: {e}")
            raise

    def check_accessibility(self, pdf_path: str) -> Optional[Dict]:
        """
        Check accessibility of a PDF file.
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            Dictionary containing accessibility report data, or None if failed
        """
        try:
            logger.info(f"Checking accessibility for: {pdf_path}")
            
            with open(pdf_path, 'rb') as pdf_file:
                input_stream = pdf_file.read()

            # Upload the PDF
            input_asset = self.pdf_services.upload(
                input_stream=input_stream,
                mime_type=PDFServicesMediaType.PDF
            )

            # Create and submit accessibility checker job
            accessibility_job = PDFAccessibilityCheckerJob(input_asset=input_asset)
            location = self.pdf_services.submit(accessibility_job)
            response = self.pdf_services.get_job_result(location, PDFAccessibilityCheckerResult)

            # Get the report
            report_asset: CloudAsset = response.get_result().get_report()
            stream_report: StreamAsset = self.pdf_services.get_content(report_asset)
            
            # Parse the JSON report
            report_data = json.loads(stream_report.get_input_stream().decode('utf-8'))
            logger.info(f"Accessibility check completed for: {pdf_path}")
            
            return report_data
            
        except (ServiceApiException, ServiceUsageException, SdkException) as e:
            logger.error(f"Failed to check accessibility for {pdf_path}: {e}")
            return None

    def autotag_pdf(self, pdf_path: str, output_dir: str, generate_report: bool = True) -> Optional[str]:
        """
        Auto-tag a PDF file to improve accessibility.
        
        Args:
            pdf_path: Path to the input PDF file
            output_dir: Directory to save the tagged PDF
            generate_report: Whether to generate a tagging report
            
        Returns:
            Path to the tagged PDF file, or None if failed
        """
        try:
            logger.info(f"Auto-tagging PDF: {pdf_path}")
            
            with open(pdf_path, 'rb') as pdf_file:
                input_stream = pdf_file.read()

            # Upload the PDF
            input_asset = self.pdf_services.upload(
                input_stream=input_stream,
                mime_type=PDFServicesMediaType.PDF
            )

            # Create parameters for auto-tagging
            autotag_params = AutotagPDFParams(
                generate_report=generate_report,
                shift_headings=True
            )

            # Create and submit auto-tag job
            autotag_job = AutotagPDFJob(
                input_asset=input_asset,
                autotag_pdf_params=autotag_params
            )
            location = self.pdf_services.submit(autotag_job)
            response = self.pdf_services.get_job_result(location, AutotagPDFResult)

            # Get the tagged PDF
            result_asset: CloudAsset = response.get_result().get_tagged_pdf()
            stream_asset: StreamAsset = self.pdf_services.get_content(result_asset)

            # Save the tagged PDF
            os.makedirs(output_dir, exist_ok=True)
            pdf_filename = Path(pdf_path).stem
            output_path = os.path.join(output_dir, f"{pdf_filename}_tagged.pdf")
            
            with open(output_path, "wb") as file:
                file.write(stream_asset.get_input_stream())

            # Optionally save the tagging report
            if generate_report:
                report_asset: CloudAsset = response.get_result().get_report()
                stream_report: StreamAsset = self.pdf_services.get_content(report_asset)
                report_path = os.path.join(output_dir, f"{pdf_filename}_tagging_report.xlsx")
                
                with open(report_path, "wb") as file:
                    file.write(stream_report.get_input_stream())
                logger.info(f"Tagging report saved to: {report_path}")

            logger.info(f"Tagged PDF saved to: {output_path}")
            return output_path
            
        except (ServiceApiException, ServiceUsageException, SdkException) as e:
            logger.error(f"Failed to auto-tag {pdf_path}: {e}")
            return None

    def process_directory(
        self,
        input_dir: str,
        output_dir: str,
        skip_initial_check: bool = False,
        output_format: str = "json"
    ) -> List[Dict]:
        """
        Process all PDFs in a directory.
        
        Args:
            input_dir: Directory containing PDF files
            output_dir: Directory to save processed files and reports
            skip_initial_check: Skip accessibility check before auto-tagging (saves API calls)
            output_format: Output format for results ('json' or 'csv')
            
        Returns:
            List of processing results for each PDF
        """
        results = []
        pdf_files = list(Path(input_dir).glob("*.pdf"))
        
        if not pdf_files:
            logger.warning(f"No PDF files found in {input_dir}")
            return results

        logger.info(f"Found {len(pdf_files)} PDF files to process")

        for pdf_path in pdf_files:
            pdf_name = pdf_path.name
            logger.info(f"\n{'='*60}")
            logger.info(f"Processing: {pdf_name}")
            logger.info(f"{'='*60}")

            result = {
                "filename": pdf_name,
                "input_path": str(pdf_path),
                "timestamp": datetime.now().isoformat(),
                "pre_tag_accessibility": None,
                "post_tag_accessibility": None,
                "tagged_pdf_path": None,
                "status": "pending"
            }

            try:
                # Step 1: Initial accessibility check (optional)
                if not skip_initial_check:
                    logger.info("Step 1/3: Running initial accessibility check...")
                    pre_check = self.check_accessibility(str(pdf_path))
                    result["pre_tag_accessibility"] = pre_check
                else:
                    logger.info("Step 1/3: Skipping initial accessibility check (API optimization)")

                # Step 2: Auto-tag the PDF
                logger.info("Step 2/3: Auto-tagging PDF...")
                tagged_path = self.autotag_pdf(
                    str(pdf_path),
                    os.path.join(output_dir, "tagged_pdfs")
                )
                result["tagged_pdf_path"] = tagged_path

                if not tagged_path:
                    result["status"] = "failed_autotagging"
                    results.append(result)
                    continue

                # Step 3: Post-tagging accessibility check
                logger.info("Step 3/3: Running post-tagging accessibility check...")
                post_check = self.check_accessibility(tagged_path)
                result["post_tag_accessibility"] = post_check

                result["status"] = "completed"
                logger.info(f"✓ Successfully processed {pdf_name}")

            except Exception as e:
                logger.error(f"Error processing {pdf_name}: {e}")
                result["status"] = "error"
                result["error"] = str(e)

            results.append(result)

        # Save results
        self._save_results(results, output_dir, output_format)
        
        return results

    def _save_results(self, results: List[Dict], output_dir: str, format: str):
        """Save processing results to file."""
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if format == "json":
            output_path = os.path.join(output_dir, f"accessibility_results_{timestamp}.json")
            with open(output_path, 'w') as f:
                json.dump(results, f, indent=2)
            logger.info(f"\n✓ Results saved to: {output_path}")
            
        elif format == "csv":
            output_path = os.path.join(output_dir, f"accessibility_results_{timestamp}.csv")
            
            # Flatten results for CSV
            csv_rows = []
            for result in results:
                row = {
                    "filename": result["filename"],
                    "status": result["status"],
                    "timestamp": result["timestamp"],
                    "tagged_pdf_path": result.get("tagged_pdf_path", ""),
                }
                
                # Add pre-tag summary
                if result.get("pre_tag_accessibility"):
                    pre_check = result["pre_tag_accessibility"]
                    row["pre_tag_compliant"] = pre_check.get("compliant", "N/A")
                    row["pre_tag_issues_count"] = len(pre_check.get("issues", []))
                
                # Add post-tag summary
                if result.get("post_tag_accessibility"):
                    post_check = result["post_tag_accessibility"]
                    row["post_tag_compliant"] = post_check.get("compliant", "N/A")
                    row["post_tag_issues_count"] = len(post_check.get("issues", []))
                
                csv_rows.append(row)
            
            # Write CSV
            if csv_rows:
                with open(output_path, 'w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=csv_rows[0].keys())
                    writer.writeheader()
                    writer.writerows(csv_rows)
                logger.info(f"\n✓ Results saved to: {output_path}")

    def print_summary(self, results: List[Dict]):
        """Print a summary of processing results."""
        total = len(results)
        completed = sum(1 for r in results if r["status"] == "completed")
        failed = total - completed
        
        logger.info(f"\n{'='*60}")
        logger.info("PROCESSING SUMMARY")
        logger.info(f"{'='*60}")
        logger.info(f"Total files processed: {total}")
        logger.info(f"Successfully completed: {completed}")
        logger.info(f"Failed: {failed}")
        logger.info(f"{'='*60}\n")


def main():    
    # ============================================================================
    # CONFIGURATION - Edit these settings for your processing needs
    # ============================================================================
    
    # Directory containing PDF files to process
    INPUT_DIRECTORY = "src/resources"
    
    # Directory to save processed files and reports
    OUTPUT_DIRECTORY = "./output"
    
    # Path to Adobe PDF Services credentials file
    CREDENTIALS_PATH = "./pdfservices-api-credentials.json"
    
    # Skip initial accessibility check before auto-tagging (saves API calls)
    # Set to True to save API usage, False to run full pre/post comparison
    SKIP_INITIAL_CHECK = False
    
    # Output format for results: "json" or "csv"
    OUTPUT_FORMAT = "json"
    
    # ============================================================================
    # END CONFIGURATION
    # ============================================================================
    
    logger.info("PDF Accessibility Batch Processor")
    logger.info("=" * 60)
    logger.info(f"Input Directory: {INPUT_DIRECTORY}")
    logger.info(f"Output Directory: {OUTPUT_DIRECTORY}")
    logger.info(f"Skip Initial Check: {SKIP_INITIAL_CHECK}")
    logger.info(f"Output Format: {OUTPUT_FORMAT}")
    logger.info("=" * 60)
    
    # Validate input directory
    if not os.path.isdir(INPUT_DIRECTORY):
        logger.error(f"Input directory does not exist: {INPUT_DIRECTORY}")
        logger.error("Please update INPUT_DIRECTORY in the main() function")
        return 1

    # Validate credentials file
    if not os.path.isfile(CREDENTIALS_PATH):
        logger.error(f"Credentials file not found: {CREDENTIALS_PATH}")
        logger.error("Please create a credentials file at the specified path")
        return 1

    try:
        # Initialize processor
        processor = PDFAccessibilityProcessor(credentials_path=CREDENTIALS_PATH)
        
        # Process directory
        results = processor.process_directory(
            input_dir=INPUT_DIRECTORY,
            output_dir=OUTPUT_DIRECTORY,
            skip_initial_check=SKIP_INITIAL_CHECK,
            output_format=OUTPUT_FORMAT
        )
        
        # Print summary
        processor.print_summary(results)
        
        return 0
        
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
