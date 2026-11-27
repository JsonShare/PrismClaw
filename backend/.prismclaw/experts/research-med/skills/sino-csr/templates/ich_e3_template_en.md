# Clinical Study Report (CSR) Template

## ICH E3 Guideline · English Version (FDA Submission)

---

> **Declaration**: This report follows the ICH E3 guideline
> (*Structure and Content of Clinical Study Reports*, CPMP/ICH/288/95),
> compliant with FDA clinical study report format requirements.

---

## Cover Page

| Field | Content |
|-------|---------|
| **Report Title** | {{REPORT_TITLE}} |
| **Protocol Number** | {{PROTOCOL_ID}} |
| **Protocol Version** | {{PROTOCOL_VERSION}} |
| **Investigational Product** | {{INVESTIGATIONAL_PRODUCT}} |
| **Indication** | {{INDICATION}} |
| **Study Phase** | {{STUDY_PHASE}} |
| **Report Type** | {{REPORT_TYPE}} (Final Report / Interim Report / Safety Report) |
| **Sponsor** | {{SPONSOR}} |
| **Principal Investigator** | {{PRINCIPAL_INVESTIGATOR}} |
| **Study Center** | {{STUDY_CENTER}} |
| **Report Date** | {{REPORT_DATE}} |
| **Declaration Type** | {{DECLARATION_TYPE}} (CONSORT/STROBE/PRISMA) |

---

## Table of Contents

{{TABLE_OF_CONTENTS}}

---

## 1. Administrative Information

### 1.1 Protocol and Amendments
- Protocol Number: {{PROTOCOL_ID}}
- Protocol Title: {{PROTOCOL_TITLE}}
- Protocol Version: {{PROTOCOL_VERSION}} (Date: {{PROTOCOL_DATE}})
- Amendments: {{AMENDMENTS_LIST}}

### 1.2 Institutional Review Board (IRB) Information

| IRB Name | Approval Date | Approval Number |
|----------|--------------|-----------------|
| {{IRB_1_NAME}} | {{IRB_1_DATE}} | {{IRB_1_NUMBER}} |

### 1.3 Trial Registration
- Clinical Trial ID: {{CLINICAL_TRIAL_ID}}
- Registry: {{REGISTRY_PLATFORM}} (e.g., ClinicalTrials.gov)
- Registration Date: {{REGISTRATION_DATE}}

### 1.4 Informed Consent
- ICF Version: {{ICF_VERSION}} (Date: {{ICF_DATE}})

---

## 2. Declarations

{{DECLARATION}}

> Per {{DECLARATION_TYPE}} guidelines, this report adheres to the following standards.

---

## 3. Investigators and Study Centers

### 3.1 Principal Investigator

| Name | Title | Institution | Contact |
|------|-------|-------------|---------|
| {{PI_NAME}} | {{PI_TITLE}} | {{PI_INSTITUTION}} | {{PI_CONTACT}} |

### 3.2 Study Center List

| Site # | Site Name | Investigator | Enrolled |
|--------|-----------|-------------|----------|
| {{CENTER_LIST}} |

---

## 4. Introduction

### 4.1 Background
{{INTRODUCTION_BACKGROUND}}

### 4.2 Investigational Product
{{DRUG_DESCRIPTION}}

### 4.3 Indication and Existing Treatments
{{INDICATION_AND_EXISTING_TREATMENT}}

### 4.4 Scientific Rationale
{{SCIENTIFIC_RATIONALE}}

### 4.5 Study Objectives (Summary)
- **Primary**: {{PRIMARY_OBJECTIVE}}
- **Secondary**: {{SECONDARY_OBJECTIVES}}
- **Exploratory**: {{EXPLORATORY_OBJECTIVES}}

---

## 5. Study Objectives

### 5.1 Primary Objectives
{{PRIMARY_OBJECTIVE_DETAIL}}

### 5.2 Secondary Objectives
{{SECONDARY_OBJECTIVES_DETAIL}}

### 5.3 Exploratory Objectives
{{EXPLORATORY_OBJECTIVES_DETAIL}}

### 5.4 Study Hypothesis
{{STUDY_HYPOTHESIS}}

---

## 6. Study Design and Methods

### 6.1 Overall Study Design
- **Study Type**: {{STUDY_TYPE}}
- **Design**: {{STUDY_DESIGN_DETAIL}}
- **Blinding**: {{BLINDING_METHOD}}
- **Phase**: {{STUDY_PHASE}}
- **Treatment Arms**: {{TREATMENT_ARMS}}

### 6.2 Study Population
#### Inclusion Criteria
{{INCLUSION_CRITERIA}}

#### Exclusion Criteria
{{EXCLUSION_CRITERIA}}

### 6.3 Treatment Regimen

| Arm | Drug | Dose | Route | Frequency | Duration |
|-----|------|------|-------|-----------|----------|
| {{TREATMENT_REGIMEN_TABLE}} |

### 6.4 Efficacy Endpoints
#### Primary Endpoint
{{PRIMARY_ENDPOINT_DEFINITION}}

#### Secondary Endpoints
{{SECONDARY_ENDPOINTS_DEFINITION}}

### 6.7 Statistical Methods
#### Sample Size Calculation
{{SAMPLE_SIZE_CALCULATION}}

> Data source: {{POWER_ANALYSIS_REFERENCE}}

#### Analysis Populations
- **Full Analysis Set (FAS)**: {{FAS_DEFINITION}}
- **Per Protocol Set (PPS)**: {{PPS_DEFINITION}}
- **Safety Set (SS)**: {{SS_DEFINITION}}

#### Primary Analysis Method
{{PRIMARY_ANALYSIS_METHOD}}

#### Multiple Comparison Correction
{{MULTIPLE_COMPARISON_CORRECTION}}

---

## 7. Study Subjects

### 7.1 Subject Disposition

| | Experimental (N={{N_EXP}}) | Control (N={{N_CTRL}}) | Total (N={{N_TOTAL}}) |
|---|---|---|---|
| Screened | {{SCREENED_EXP}} | {{SCREENED_CTRL}} | {{SCREENED_TOTAL}} |
| Randomized | {{RANDOMIZED_EXP}} | {{RANDOMIZED_CTRL}} | {{RANDOMIZED_TOTAL}} |
| Completed | {{COMPLETED_EXP}} | {{COMPLETED_CTRL}} | {{COMPLETED_TOTAL}} |
| Discontinued | {{DISCONTINUED_EXP}} | {{DISCONTINUED_CTRL}} | {{DISCONTINUED_TOTAL}} |

> Data source: {{DATA_PREPARATION_REFERENCE}}

#### CONSORT Flow Diagram
{{CONSORT_FLOW_DIAGRAM}}

### 7.2 Protocol Deviations
{{PROTOCOL_DEVIATIONS_TABLE}}

---

## 8. Efficacy Analysis

### 8.2 Demographics and Baseline Characteristics (Table 1)

> Data source: {{DIFFERENTIAL_ANALYSIS_REFERENCE}}

{{DEMOGRAPHICS_TABLE}}

### 8.5 Primary Endpoint Analysis

| Statistic | Experimental | Control | Difference (95% CI) | P-value |
|-----------|-------------|---------|-------------------|---------|
| {{PRIMARY_ENDPOINT_RESULTS}} |

> Effect size: {{PRIMARY_EFFECT_SIZE}}

### 8.8 Multivariable Analysis

> Data source: {{REGRESSION_ANALYSIS_REFERENCE}}

{{REGRESSION_RESULTS}}

### 8.9 Survival Analysis (if applicable)

> Data source: {{SURVIVAL_ANALYSIS_REFERENCE}}

{{SURVIVAL_RESULTS}}

### 8.10 Efficacy Conclusions

> Plain Language interpretation:
> {{PLAIN_LANGUAGE_EFFICACY}}

---

## 9. Safety Analysis

> **This section is mandatory for CSR and cannot be omitted.**

### 9.3 Adverse Events Summary

| | Experimental (N={{N_EXP}}) | Control (N={{N_CTRL}}) |
|---|---|---|
| Any AE | {{ANY_AE_EXP}} (%) | {{ANY_AE_CTRL}} (%) |
| Related AE | {{RELATED_AE_EXP}} (%) | {{RELATED_AE_CTRL}} (%) |
| SAE | {{SAE_EXP}} (%) | {{SAE_CTRL}} (%) |

### 9.11 Safety Conclusions
{{SAFETY_CONCLUSION}}

---

## 13. Discussion and Conclusions

### 13.3 Conclusions

> Plain Language interpretation:
> {{PLAIN_LANGUAGE_CONCLUSION}}

---

## 14. List of Tables and Figures

> Data source: {{CHART_GENERATOR_REFERENCE}}

| # | Title | Page |
|---|-------|------|
| {{LIST_OF_TABLES}} |

| # | Title | Page |
|---|-------|------|
| {{LIST_OF_FIGURES}} |

---

## 15. References

{{REFERENCES}}

> Citation format: Vancouver (FDA submission)

---

## 16. Appendices

### Appendix B: Statistical Analysis Plan (SAP)
{{APPENDIX_SAP}}

### Appendix C: Statistical Analysis Code
{{APPENDIX_CODE}}

### Appendix D: Case Report Form (CRF)
{{APPENDIX_CRF}}

---

## 19. Signatures

| Role | Name | Signature | Date |
|------|------|-----------|------|
| Principal Investigator | {{PI_NAME}} | | |
| Statistician | {{STATISTICIAN_NAME}} | | |
| Sponsor Representative | {{SPONSOR_REP_NAME}} | | |

---

> **Glossary**: {{GLOSSARY}}

---

*This report follows ICH E3 guideline (CPMP/ICH/288/95) and FDA clinical study report format requirements.*
