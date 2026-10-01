Attribute VB_Name = "BispEpmPipeline"
Option Explicit

Private Const RUNNER_SHEET As String = "Pipeline Runner"
Private Const TOKEN_ENVIRONMENT_VARIABLE As String = "BISP_EPM_EXCEL_TOKEN"

Private Const CELL_BACKEND_URL As String = "B7"
Private Const CELL_PIPELINE_CODE As String = "B9"
Private Const CELL_POLL_SECONDS As String = "B10"
Private Const CELL_TIMEOUT_SECONDS As String = "B11"

Private Const VARIABLE_FIRST_ROW As Long = 15
Private Const VARIABLE_LAST_ROW As Long = 26
Private Const VARIABLE_NAME_COLUMN As Long = 1
Private Const VARIABLE_VALUE_COLUMN As Long = 2

Private Const FILE_FIRST_ROW As Long = 15
Private Const FILE_LAST_ROW As Long = 26
Private Const FILE_KEY_COLUMN As Long = 4
Private Const FILE_VALUE_COLUMN As Long = 5

Private Const CELL_STATUS As String = "B30"
Private Const CELL_EXECUTION_ID As String = "B31"
Private Const CELL_OPERATION As String = "B32"
Private Const CELL_STARTED As String = "B33"
Private Const CELL_COMPLETED As String = "B34"
Private Const CELL_LAST_CHECKED As String = "B35"
Private Const CELL_ERROR As String = "B36"
Private Const HISTORY_FIRST_ROW As Long = 43


Public Sub RunPipelineFromExcel()
    Dim runner As Worksheet
    Dim backendUrl As String
    Dim pipelineCode As String
    Dim token As String
    Dim variablesJson As String
    Dim inboxFilesJson As String
    Dim requestBody As String
    Dim responseBody As String
    Dim executionId As String
    Dim statusUrl As String
    Dim executionStatus As String
    Dim errorMessage As String
    Dim pollSeconds As Long
    Dim timeoutSeconds As Long
    Dim startedWaiting As Date
    Dim historyRow As Long

    On Error GoTo Failed
    Set runner = ThisWorkbook.Worksheets(RUNNER_SHEET)

    backendUrl = NormalizeBackendUrl(CStr(runner.Range(CELL_BACKEND_URL).Value2))
    pipelineCode = Trim$(CStr(runner.Range(CELL_PIPELINE_CODE).Value2))
    pollSeconds = ReadBoundedLong(runner, CELL_POLL_SECONDS, 1, 30)
    timeoutSeconds = ReadBoundedLong(runner, CELL_TIMEOUT_SECONDS, 30, 7200)
    token = Trim$(Environ$(TOKEN_ENVIRONMENT_VARIABLE))

    If backendUrl = "" Then
        Err.Raise vbObjectError + 1000, , "Enter the backend URL in cell " & CELL_BACKEND_URL & "."
    End If
    If Not IsSafePipelineCode(pipelineCode) Then
        Err.Raise vbObjectError + 1001, , "Enter a valid Pipeline code using letters, numbers, dots, hyphens, or underscores."
    End If
    If token = "" Then
        Err.Raise vbObjectError + 1002, , _
            "The Windows environment variable " & TOKEN_ENVIRONMENT_VARIABLE & " is not configured. " & _
            "Follow the setup instructions and restart Excel."
    End If

    variablesJson = JsonObjectFromRows( _
        runner, VARIABLE_FIRST_ROW, VARIABLE_LAST_ROW, _
        VARIABLE_NAME_COLUMN, VARIABLE_VALUE_COLUMN, "Pipeline variable")
    inboxFilesJson = JsonObjectFromRows( _
        runner, FILE_FIRST_ROW, FILE_LAST_ROW, _
        FILE_KEY_COLUMN, FILE_VALUE_COLUMN, "Inbox file input")

    If MsgBox( _
        BuildConfirmation(runner, pipelineCode), _
        vbQuestion + vbYesNo + vbDefaultButton2, _
        "Confirm Oracle Pipeline execution") <> vbYes Then
        SetStatus runner, "CANCELLED", "No request was sent."
        Exit Sub
    End If

    ClearExecutionResult runner
    SetStatus runner, "PREFLIGHT", "Validating the live Pipeline definition..."
    Application.StatusBar = "BISP EPM: validating Pipeline " & pipelineCode & "..."

    responseBody = SendApiRequest( _
        "GET", _
        backendUrl & "/api/v1/excel/pipelines/" & pipelineCode & "/preflight", _
        token, _
        "")

    requestBody = "{""variables"":" & variablesJson & _
        ",""inbox_files"":" & inboxFilesJson & _
        ",""confirmed"":true}"

    SetStatus runner, "SUBMITTING", "Preflight passed. Submitting the governed run..."
    Application.StatusBar = "BISP EPM: submitting Pipeline " & pipelineCode & "..."
    responseBody = SendApiRequest( _
        "POST", _
        backendUrl & "/api/v1/excel/pipelines/" & pipelineCode & "/runs", _
        token, _
        requestBody)

    executionId = JsonString(responseBody, "execution_id")
    statusUrl = JsonString(responseBody, "status_url")
    If executionId = "" Or statusUrl = "" Then
        Err.Raise vbObjectError + 1003, , "The platform accepted the request but did not return an execution reference."
    End If

    runner.Range(CELL_EXECUTION_ID).Value = executionId
    runner.Range(CELL_OPERATION).Value = "Pipeline " & pipelineCode
    SetStatus runner, "QUEUED", "The Pipeline was accepted by the platform."
    historyRow = AppendHistory(runner, pipelineCode, executionId, "QUEUED", "Accepted by the platform")
    startedWaiting = Now

    Do
        If DateDiff("s", startedWaiting, Now) > timeoutSeconds Then
            Err.Raise vbObjectError + 1004, , _
                "Excel stopped waiting after " & timeoutSeconds & _
                " seconds. Execution " & executionId & _
                " may still be running; check Execution History in the platform."
        End If

        Application.StatusBar = "BISP EPM: monitoring execution " & executionId & "..."
        Application.Wait Now + TimeSerial(0, 0, pollSeconds)
        DoEvents

        responseBody = SendApiRequest("GET", backendUrl & statusUrl, token, "")
        executionStatus = UCase$(JsonString(responseBody, "status"))
        errorMessage = JsonString(responseBody, "error_message")

        runner.Range(CELL_STATUS).Value = executionStatus
        runner.Range(CELL_OPERATION).Value = JsonString(responseBody, "operation_name")
        runner.Range(CELL_STARTED).Value = JsonString(responseBody, "started_at")
        runner.Range(CELL_COMPLETED).Value = JsonString(responseBody, "completed_at")
        runner.Range(CELL_LAST_CHECKED).Value = Now
        runner.Range(CELL_ERROR).Value = errorMessage

        If executionStatus = "SUCCESS" Or executionStatus = "FAILED" Then Exit Do
    Loop

    UpdateHistory runner, historyRow, executionStatus, errorMessage
    Application.StatusBar = False
    If executionStatus = "SUCCESS" Then
        MsgBox "Oracle Pipeline " & pipelineCode & " completed successfully." & vbCrLf & _
            "Execution ID: " & executionId, vbInformation, "Pipeline completed"
    Else
        MsgBox "Oracle Pipeline " & pipelineCode & " failed." & vbCrLf & _
            IIf(errorMessage = "", "Review the platform execution log.", errorMessage), _
            vbExclamation, "Pipeline failed"
    End If
    Exit Sub

Failed:
    Application.StatusBar = False
    If Not runner Is Nothing Then
        SetStatus runner, "ERROR", Err.Description
        runner.Range(CELL_ERROR).Value = Err.Description
        If historyRow >= HISTORY_FIRST_ROW Then
            UpdateHistory runner, historyRow, "ERROR", Err.Description
        End If
    End If
    MsgBox Err.Description, vbCritical, "BISP EPM Pipeline Runner"
End Sub


Public Sub CheckPipelineConnection()
    Dim runner As Worksheet
    Dim backendUrl As String
    Dim pipelineCode As String
    Dim token As String
    Dim responseBody As String

    On Error GoTo Failed
    Set runner = ThisWorkbook.Worksheets(RUNNER_SHEET)
    backendUrl = NormalizeBackendUrl(CStr(runner.Range(CELL_BACKEND_URL).Value2))
    pipelineCode = Trim$(CStr(runner.Range(CELL_PIPELINE_CODE).Value2))
    token = Trim$(Environ$(TOKEN_ENVIRONMENT_VARIABLE))
    If backendUrl = "" Or Not IsSafePipelineCode(pipelineCode) Or token = "" Then
        Err.Raise vbObjectError + 1010, , _
            "Complete the backend URL, Pipeline code, and API-token setup first."
    End If

    SetStatus runner, "PREFLIGHT", "Checking token and live Pipeline access..."
    responseBody = SendApiRequest( _
        "GET", _
        backendUrl & "/api/v1/excel/pipelines/" & pipelineCode & "/preflight", _
        token, _
        "")
    SetStatus runner, "READY", "Connection and Pipeline preflight succeeded."
    MsgBox "Connection successful. Pipeline " & pipelineCode & _
        " is available to this token.", vbInformation, "Preflight successful"
    Exit Sub

Failed:
    If Not runner Is Nothing Then SetStatus runner, "ERROR", Err.Description
    MsgBox Err.Description, vbCritical, "BISP EPM connection check"
End Sub


Private Function SendApiRequest( _
    ByVal method As String, _
    ByVal url As String, _
    ByVal token As String, _
    ByVal body As String) As String

    Dim request As Object
    Set request = CreateObject("WinHttp.WinHttpRequest.5.1")
    request.SetTimeouts 10000, 10000, 30000, 30000
    request.Open method, url, False
    request.SetRequestHeader "Accept", "application/json"
    request.SetRequestHeader "Authorization", "Bearer " & token
    If method = "POST" Then request.SetRequestHeader "Content-Type", "application/json; charset=utf-8"
    request.Send body

    If request.Status < 200 Or request.Status >= 300 Then
        Err.Raise vbObjectError + 1100 + request.Status, , _
            "Platform request failed (HTTP " & request.Status & "): " & _
            ApiErrorMessage(CStr(request.ResponseText))
    End If
    SendApiRequest = CStr(request.ResponseText)
End Function


Private Function JsonObjectFromRows( _
    ByVal sheet As Worksheet, _
    ByVal firstRow As Long, _
    ByVal lastRow As Long, _
    ByVal nameColumn As Long, _
    ByVal valueColumn As Long, _
    ByVal label As String) As String

    Dim values As Object
    Dim rowNumber As Long
    Dim name As String
    Dim value As String
    Dim result As String
    Dim key As Variant

    Set values = CreateObject("Scripting.Dictionary")
    values.CompareMode = vbTextCompare
    For rowNumber = firstRow To lastRow
        name = Trim$(CStr(sheet.Cells(rowNumber, nameColumn).Value2))
        value = Trim$(CStr(sheet.Cells(rowNumber, valueColumn).Value2))
        If name <> "" Or value <> "" Then
            If name = "" Or value = "" Then
                Err.Raise vbObjectError + 1200, , _
                    label & " row " & rowNumber & " requires both a name and a value."
            End If
            If values.Exists(name) Then
                Err.Raise vbObjectError + 1201, , _
                    label & " '" & name & "' was entered more than once."
            End If
            values.Add name, value
        End If
    Next rowNumber

    result = "{"
    For Each key In values.Keys
        If Len(result) > 1 Then result = result & ","
        result = result & """" & JsonEscape(CStr(key)) & """:""" & _
            JsonEscape(CStr(values(key))) & """"
    Next key
    JsonObjectFromRows = result & "}"
End Function


Private Function JsonEscape(ByVal value As String) As String
    value = Replace(value, "\", "\\")
    value = Replace(value, """", "\""")
    value = Replace(value, vbCr, "\r")
    value = Replace(value, vbLf, "\n")
    value = Replace(value, vbTab, "\t")
    JsonEscape = value
End Function


Private Function JsonString(ByVal json As String, ByVal key As String) As String
    Dim markerPosition As Long
    Dim valuePosition As Long
    Dim character As String
    Dim result As String
    Dim escaped As Boolean
    Dim unicodeValue As String

    markerPosition = InStr(1, json, """" & key & """", vbTextCompare)
    If markerPosition = 0 Then Exit Function
    valuePosition = InStr(markerPosition, json, ":") + 1
    Do While valuePosition <= Len(json) And _
        InStr(1, " " & vbTab & vbCr & vbLf, _
            Mid$(json, valuePosition, 1), vbBinaryCompare) > 0
        valuePosition = valuePosition + 1
    Loop
    If LCase$(Mid$(json, valuePosition, 4)) = "null" Then Exit Function
    If Mid$(json, valuePosition, 1) <> """" Then Exit Function
    valuePosition = valuePosition + 1

    Do While valuePosition <= Len(json)
        character = Mid$(json, valuePosition, 1)
        If escaped Then
            Select Case character
                Case """", "\", "/": result = result & character
                Case "b": result = result & Chr$(8)
                Case "f": result = result & Chr$(12)
                Case "n": result = result & vbLf
                Case "r": result = result & vbCr
                Case "t": result = result & vbTab
                Case "u"
                    unicodeValue = Mid$(json, valuePosition + 1, 4)
                    If Len(unicodeValue) = 4 Then
                        result = result & ChrW$(CLng("&H" & unicodeValue))
                        valuePosition = valuePosition + 4
                    End If
                Case Else: result = result & character
            End Select
            escaped = False
        ElseIf character = "\" Then
            escaped = True
        ElseIf character = """" Then
            Exit Do
        Else
            result = result & character
        End If
        valuePosition = valuePosition + 1
    Loop
    JsonString = result
End Function


Private Function ApiErrorMessage(ByVal responseBody As String) As String
    Dim message As String
    message = JsonString(responseBody, "details")
    If message = "" Then message = JsonString(responseBody, "detail")
    If message = "" Then message = JsonString(responseBody, "message")
    If message = "" Then message = Left$(responseBody, 500)
    ApiErrorMessage = message
End Function


Private Function NormalizeBackendUrl(ByVal value As String) As String
    value = Trim$(value)
    Do While Right$(value, 1) = "/"
        value = Left$(value, Len(value) - 1)
    Loop
    If value <> "" And LCase$(Left$(value, 7)) <> "http://" And _
        LCase$(Left$(value, 8)) <> "https://" Then
        Err.Raise vbObjectError + 1300, , "Backend URL must begin with http:// or https://."
    End If
    NormalizeBackendUrl = value
End Function


Private Function IsSafePipelineCode(ByVal value As String) As Boolean
    Dim expression As Object
    Set expression = CreateObject("VBScript.RegExp")
    expression.Pattern = "^[A-Za-z0-9][A-Za-z0-9._-]{0,49}$"
    IsSafePipelineCode = expression.Test(value)
End Function


Private Function ReadBoundedLong( _
    ByVal sheet As Worksheet, _
    ByVal address As String, _
    ByVal minimum As Long, _
    ByVal maximum As Long) As Long

    Dim value As Variant
    value = sheet.Range(address).Value2
    If Not IsNumeric(value) Then
        Err.Raise vbObjectError + 1400, , "Cell " & address & " must contain a number."
    End If
    ReadBoundedLong = CLng(value)
    If ReadBoundedLong < minimum Or ReadBoundedLong > maximum Then
        Err.Raise vbObjectError + 1401, , "Cell " & address & " must be between " & minimum & " and " & maximum & "."
    End If
End Function


Private Function BuildConfirmation( _
    ByVal sheet As Worksheet, _
    ByVal pipelineCode As String) As String

    BuildConfirmation = "You are about to run Oracle Pipeline " & pipelineCode & "." & vbCrLf & vbCrLf & _
        "Year: " & FindVariableValue(sheet, "YEAR") & vbCrLf & _
        "Start period: " & FindVariableValue(sheet, "STARTPERIOD") & vbCrLf & _
        "End period: " & FindVariableValue(sheet, "ENDPERIOD") & vbCrLf & vbCrLf & _
        "The backend will perform live preflight and record this execution as Excel. Continue?"
End Function


Private Function FindVariableValue( _
    ByVal sheet As Worksheet, _
    ByVal variableName As String) As String

    Dim rowNumber As Long
    For rowNumber = VARIABLE_FIRST_ROW To VARIABLE_LAST_ROW
        If StrComp(Trim$(CStr(sheet.Cells(rowNumber, VARIABLE_NAME_COLUMN).Value2)), variableName, vbTextCompare) = 0 Then
            FindVariableValue = Trim$(CStr(sheet.Cells(rowNumber, VARIABLE_VALUE_COLUMN).Value2))
            Exit Function
        End If
    Next rowNumber
    FindVariableValue = "(not supplied)"
End Function


Private Sub ClearExecutionResult(ByVal sheet As Worksheet)
    sheet.Range(CELL_STATUS).Value = "PREPARING"
    sheet.Range(CELL_EXECUTION_ID).ClearContents
    sheet.Range(CELL_OPERATION).ClearContents
    sheet.Range(CELL_STARTED).ClearContents
    sheet.Range(CELL_COMPLETED).ClearContents
    sheet.Range(CELL_LAST_CHECKED).ClearContents
    sheet.Range(CELL_ERROR).ClearContents
End Sub


Private Sub SetStatus( _
    ByVal sheet As Worksheet, _
    ByVal status As String, _
    ByVal details As String)

    sheet.Range(CELL_STATUS).Value = status
    sheet.Range(CELL_LAST_CHECKED).Value = Now
    sheet.Range(CELL_ERROR).Value = details
End Sub


Private Function AppendHistory( _
    ByVal sheet As Worksheet, _
    ByVal pipelineCode As String, _
    ByVal executionId As String, _
    ByVal status As String, _
    ByVal details As String) As Long

    Dim rowNumber As Long
    rowNumber = sheet.Cells(sheet.Rows.Count, 1).End(xlUp).Row + 1
    If rowNumber < HISTORY_FIRST_ROW Then rowNumber = HISTORY_FIRST_ROW
    sheet.Cells(rowNumber, 1).Value = Now
    sheet.Cells(rowNumber, 2).Value = pipelineCode
    sheet.Cells(rowNumber, 3).Value = FindVariableValue(sheet, "YEAR")
    sheet.Cells(rowNumber, 4).Value = FindVariableValue(sheet, "STARTPERIOD")
    sheet.Cells(rowNumber, 5).Value = FindVariableValue(sheet, "ENDPERIOD")
    sheet.Cells(rowNumber, 6).Value = executionId
    sheet.Cells(rowNumber, 7).Value = status
    sheet.Cells(rowNumber, 8).Value = details
    AppendHistory = rowNumber
End Function


Private Sub UpdateHistory( _
    ByVal sheet As Worksheet, _
    ByVal rowNumber As Long, _
    ByVal status As String, _
    ByVal details As String)

    sheet.Cells(rowNumber, 7).Value = status
    sheet.Cells(rowNumber, 8).Value = IIf(details = "", "Completed", details)
End Sub
